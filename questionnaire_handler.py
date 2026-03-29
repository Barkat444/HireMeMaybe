import os
import re
import json
import time
import random
import logging
import requests
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from utils import save_screenshot

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEBUG_DIR = os.path.join(SCRIPT_DIR, "debug")
SCREENING_ANSWERS_FILE = os.path.join(SCRIPT_DIR, "screening_answers.json")
QA_LOG_FILE = os.path.join(DEBUG_DIR, "qa_log.json")
LEARNED_SELECTORS_FILE = os.path.join(DEBUG_DIR, "learned_selectors.json")

os.makedirs(DEBUG_DIR, exist_ok=True)

_screening_config = None
_resume_text_cache = None
_selector_cache = None

# ---------------------------------------------------------------------------
# Verified Naukri chatbot DOM selectors (sourced from working automation repos)
#
# Chat list container : ul[id*='chatList_']
# Bot message item    : li.botItem.chatbot_ListItem   (class contains 'botItem')
# Bot message text    : li[class*='botItem'] > div > div > span
# Text input          : div.textArea  (contenteditable div, NOT <input>)
# Radio container     : .ssrc__radio-btn-container
# Radio label         : .ssrc__radio-btn-container label
# Radio input         : .ssrc__radio-btn-container input
# Save/submit button  : /html/body/div[2]/div/div[1]/div[3]/div/div  (absolute XPath)
# Success message     : span[class*='apply-message'] containing "successfully applied"
# Success header      : div[class*='apply-status-header'][class*='green']
# Already applied     : #already-applied
# DOB input           : ul[id*='dob__input-container']
# ---------------------------------------------------------------------------


def _load_screening_config():
    """Load and cache the screening answers config."""
    global _screening_config
    if _screening_config is not None:
        return _screening_config

    if not os.path.exists(SCREENING_ANSWERS_FILE):
        logging.warning(f"Screening answers file not found: {SCREENING_ANSWERS_FILE}")
        _screening_config = {"profile": {}, "questions": []}
        return _screening_config

    with open(SCREENING_ANSWERS_FILE, "r", encoding="utf-8") as f:
        _screening_config = json.load(f)

    logging.info(f"Loaded screening config: {len(_screening_config.get('questions', []))} question patterns")
    return _screening_config


def load_resume_text():
    """Extract text from the resume PDF in the project folder. Caches for the session."""
    global _resume_text_cache
    if _resume_text_cache is not None:
        return _resume_text_cache

    try:
        from PyPDF2 import PdfReader
    except ImportError:
        logging.warning("PyPDF2 not installed, resume context unavailable for Ollama")
        _resume_text_cache = ""
        return _resume_text_cache

    resume_keywords = ["resume", "cv", "curriculum", "vitae"]
    pdf_files = [f for f in os.listdir(SCRIPT_DIR) if f.lower().endswith(".pdf")]

    target_pdf = None
    for keyword in resume_keywords:
        for pdf in pdf_files:
            if keyword in pdf.lower():
                target_pdf = os.path.join(SCRIPT_DIR, pdf)
                break
        if target_pdf:
            break

    if not target_pdf and pdf_files:
        target_pdf = os.path.join(SCRIPT_DIR, pdf_files[0])

    if not target_pdf:
        logging.warning("No resume PDF found in project folder")
        _resume_text_cache = ""
        return _resume_text_cache

    try:
        reader = PdfReader(target_pdf)
        pages_text = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages_text.append(text)
        _resume_text_cache = "\n".join(pages_text).strip()
        logging.info(f"Loaded resume text from {os.path.basename(target_pdf)} ({len(_resume_text_cache)} chars)")
    except Exception as e:
        logging.error(f"Failed to extract text from resume PDF: {e}")
        _resume_text_cache = ""

    return _resume_text_cache


def _keyword_matches(kw_lower, question_lower):
    """Check if keyword appears in question text.
    Short keywords (<=5 chars) require word-boundary matching to avoid
    false positives like 'age' matching 'package' or 'percentage'."""
    if kw_lower not in question_lower:
        return False
    if len(kw_lower) <= 5:
        return bool(re.search(r'\b' + re.escape(kw_lower) + r'\b', question_lower))
    return True


def match_config(question_text):
    """Match question text against screening_answers.json keyword patterns.
    Uses longest-match-wins strategy so specific keywords beat generic ones.
    Returns (answer_value, answer_key, answer_type) or (None, None, None)."""
    config = _load_screening_config()
    question_lower = question_text.lower().strip()

    best_match = None
    best_keyword_len = 0

    for entry in config.get("questions", []):
        for keyword in entry["keywords"]:
            kw_lower = keyword.lower()
            if _keyword_matches(kw_lower, question_lower) and len(kw_lower) > best_keyword_len:
                answer_key = entry["answer_key"]
                answer_value = config["profile"].get(answer_key, "")
                if answer_value:
                    best_match = (answer_value, answer_key, entry.get("type", "text"))
                    best_keyword_len = len(kw_lower)

    return best_match if best_match else (None, None, None)


def _sanitize_ollama_answer(answer):
    """Clean up Ollama's response for safe typing into a chatbot input.
    Strips newlines, meta-commentary, and excessive length."""
    if not answer:
        return ""

    answer = " ".join(answer.split())

    discard_phrases = [
        "not mentioned", "no information", "not available", "not specified",
        "cannot determine", "n/a", "the resume", "the candidate's resume",
        "based on the resume", "according to the resume",
    ]
    answer_lower = answer.lower()
    for phrase in discard_phrases:
        if phrase in answer_lower and len(answer) > 50:
            return ""

    if len(answer) > 200:
        answer = answer[:200].rsplit(" ", 1)[0]

    return answer.strip()


def ask_ollama(question_text, options, resume_context):
    """Call the local Ollama instance to answer a question.
    Returns a sanitized answer string, or empty string on failure."""
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

    options_text = ""
    if options:
        options_text = "\nAvailable options:\n" + "\n".join(f"- {opt}" for opt in options)
        options_text += "\n\nYou MUST pick exactly one option from the list above. Return ONLY the option text, nothing else."

    resume_section = ""
    if resume_context:
        resume_section = f"\n\nCandidate Resume (use for context only):\n{resume_context[:3000]}"

    system_prompt = (
        "You are filling out a job application screening form on behalf of a candidate. "
        "Rules you MUST follow:\n"
        "1. Answer in 1-5 words maximum. Be extremely concise.\n"
        "2. For numeric questions (years, CTC, etc.), return ONLY the number.\n"
        "3. For yes/no questions, return ONLY 'Yes' or 'No'.\n"
        "4. For multiple choice, return ONLY the exact option text.\n"
        "5. For skill/tool questions, list relevant ones separated by commas on a single line.\n"
        "6. NEVER include explanations, preamble, reasoning, or meta-commentary.\n"
        "7. NEVER expose personal information like email addresses, phone numbers, or home addresses.\n"
        "8. NEVER say things like 'based on the resume' or 'not mentioned in the resume'.\n"
        "9. If you don't know the answer, make a reasonable positive assumption.\n"
        "10. Always answer on a SINGLE LINE, never use newlines or bullet points."
    )

    user_prompt = f"Question: {question_text}{options_text}{resume_section}"

    try:
        response = requests.post(
            f"{ollama_url}/api/chat",
            json={
                "model": ollama_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.1},
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        raw_answer = data.get("message", {}).get("content", "").strip()
        answer = _sanitize_ollama_answer(raw_answer)
        logging.info(f"Ollama answered: '{answer}' (raw: '{raw_answer[:80]}') for question: '{question_text[:80]}'")
        return answer
    except requests.exceptions.ConnectionError:
        logging.error("Cannot connect to Ollama. Is it running? Check OLLAMA_URL in .env")
        return ""
    except Exception as e:
        logging.error(f"Ollama request failed: {e}")
        return ""


def _ask_ollama_page_analysis(page_context, task_description):
    """Use Ollama to analyze page DOM/text when selectors fail.
    Returns the AI's response string."""
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

    system_prompt = (
        "You are a web automation assistant analyzing a webpage's visible text. "
        "The user will give you the page text and ask you to extract specific information. "
        "Be precise. Return ONLY what is asked, no explanations."
    )

    try:
        response = requests.post(
            f"{ollama_url}/api/chat",
            json={
                "model": ollama_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"{task_description}\n\nPage text:\n{page_context[:4000]}"},
                ],
                "stream": False,
                "options": {"temperature": 0.1},
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("message", {}).get("content", "").strip()
    except Exception as e:
        logging.debug(f"Ollama page analysis failed: {e}")
        return ""


def log_qa(job_title, company, question_text, input_type, answer, source, config_key=None):
    """Append a question/answer entry to qa_log.json for self-learning."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "job_title": job_title,
        "company": company,
        "question": question_text,
        "input_type": input_type,
        "answer": answer,
        "source": source,
        "config_key": config_key,
    }

    log_data = []
    if os.path.exists(QA_LOG_FILE):
        try:
            with open(QA_LOG_FILE, "r", encoding="utf-8") as f:
                log_data = json.load(f)
        except (json.JSONDecodeError, ValueError):
            log_data = []

    log_data.append(entry)

    with open(QA_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2, ensure_ascii=False)


def _fuzzy_match_option(answer, options):
    """Find the best matching option from a list given an answer string.
    Tries exact match first, then substring, then word overlap."""
    if not options:
        return None

    answer_lower = answer.lower().strip()

    for opt in options:
        if opt.lower().strip() == answer_lower:
            return opt

    for opt in options:
        if answer_lower in opt.lower() or opt.lower() in answer_lower:
            return opt

    best_match = None
    best_score = 0
    answer_words = set(answer_lower.split())
    for opt in options:
        opt_words = set(opt.lower().split())
        overlap = len(answer_words & opt_words)
        if overlap > best_score:
            best_score = overlap
            best_match = opt

    return best_match if best_score > 0 else options[0]


# ---------------------------------------------------------------------------
#  DOM SNAPSHOT EXTRACTOR  (structured context for AI analysis)
# ---------------------------------------------------------------------------

_DOM_SNAPSHOT_JS = """
(function(root, maxLen) {
    var out = [];
    var len = 0;
    function walk(el, depth) {
        if (len > maxLen || depth > 8) return;
        if (!el || el.nodeType !== 1) return;
        var style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return;
        var tag = el.tagName.toLowerCase();
        var skip = {'script':1,'style':1,'noscript':1,'svg':1,'path':1,'link':1,'meta':1,'head':1};
        if (skip[tag]) return;
        var indent = '  '.repeat(depth);
        var attrs = [];
        if (el.id) attrs.push('id="' + el.id + '"');
        if (el.className && typeof el.className === 'string') {
            var cls = el.className.trim();
            if (cls) attrs.push('class="' + cls.substring(0, 80) + '"');
        }
        if (el.type) attrs.push('type="' + el.type + '"');
        if (el.placeholder) attrs.push('placeholder="' + el.placeholder.substring(0, 50) + '"');
        if (el.contentEditable === 'true') attrs.push('contenteditable');
        if (el.getAttribute('role')) attrs.push('role="' + el.getAttribute('role') + '"');
        if (el.value && tag === 'input') attrs.push('value="' + el.value.substring(0, 30) + '"');
        var text = '';
        for (var c = el.firstChild; c; c = c.nextSibling) {
            if (c.nodeType === 3) text += c.textContent;
        }
        text = text.trim().substring(0, 100);
        var line = indent + '<' + tag + (attrs.length ? ' ' + attrs.join(' ') : '') + '>';
        if (text) line += ' ' + JSON.stringify(text);
        out.push(line);
        len += line.length;
        var children = el.children;
        for (var i = 0; i < children.length && len < maxLen; i++) {
            walk(children[i], depth + 1);
        }
    }
    walk(root, 0);
    return out.join('\\n');
})(arguments[0], arguments[1]);
"""


def _extract_dom_snapshot(driver, root_element=None, max_chars=3500):
    """Extract a lightweight, structured DOM tree for AI analysis.
    Captures tag, class, id, placeholder, contenteditable, type, and direct text."""
    try:
        root = root_element or driver.find_element(By.TAG_NAME, "body")
        snapshot = driver.execute_script(_DOM_SNAPSHOT_JS, root, max_chars)
        return snapshot or ""
    except Exception as e:
        logging.debug(f"DOM snapshot extraction failed: {e}")
        try:
            return (root_element or driver).text[:2000]
        except Exception:
            return ""


# ---------------------------------------------------------------------------
#  SELF-LEARNING SELECTOR CACHE
# ---------------------------------------------------------------------------

def _load_selector_cache():
    """Load cached selectors that worked in previous runs."""
    global _selector_cache
    if _selector_cache is not None:
        return _selector_cache

    if os.path.exists(LEARNED_SELECTORS_FILE):
        try:
            with open(LEARNED_SELECTORS_FILE, "r", encoding="utf-8") as f:
                _selector_cache = json.load(f)
                logging.info(f"Loaded {len(_selector_cache)} learned selectors")
                return _selector_cache
        except Exception:
            pass

    _selector_cache = {}
    return _selector_cache


def _save_learned_selector(purpose, selector_type, selector_value):
    """Save a working selector to the cache for future runs.
    purpose: e.g. 'chat_list', 'text_input', 'save_button', 'bot_question'
    selector_type: 'css' or 'xpath'
    """
    cache = _load_selector_cache()

    if purpose not in cache:
        cache[purpose] = []

    entry = {"type": selector_type, "selector": selector_value}
    if entry not in cache[purpose]:
        cache[purpose].insert(0, entry)
        cache[purpose] = cache[purpose][:5]

    cache["_last_updated"] = datetime.now().isoformat()

    try:
        with open(LEARNED_SELECTORS_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
        logging.info(f"Learned selector [{purpose}]: {selector_type}='{selector_value}'")
    except Exception as e:
        logging.debug(f"Failed to save learned selector: {e}")


def _try_learned_selectors(driver, purpose):
    """Try previously learned selectors for a given purpose.
    Returns the first visible element found, or None."""
    cache = _load_selector_cache()
    entries = cache.get(purpose, [])

    for entry in entries:
        sel_type = entry.get("type", "css")
        sel_val = entry.get("selector", "")
        if not sel_val:
            continue
        try:
            by = By.CSS_SELECTOR if sel_type == "css" else By.XPATH
            elements = driver.find_elements(by, sel_val)
            for el in elements:
                if el.is_displayed():
                    logging.info(f"Learned selector hit [{purpose}]: {sel_type}='{sel_val}'")
                    return el
        except Exception:
            continue

    return None


# ---------------------------------------------------------------------------
#  AI-POWERED ELEMENT FINDING (universal "get unstuck" layer)
# ---------------------------------------------------------------------------

def _ai_find_element(driver, root_element, purpose, description):
    """Ask AI to locate an element by analyzing the DOM snapshot.
    Returns (element, selector_used) or (None, None).

    The AI returns a CSS selector or XPath, which we then use to find the element.
    If found, the selector is cached for future runs.
    """
    snapshot = _extract_dom_snapshot(driver, root_element)
    if not snapshot:
        return None, None

    prompt = (
        f"Analyze this DOM tree from a Naukri.com job application page. "
        f"I need to find: {description}\n"
        f"Return ONLY a CSS selector that would match this element. "
        f"If you can't determine a CSS selector, return an XPath starting with //. "
        f"Return ONLY the selector string, nothing else.\n\n"
        f"DOM:\n{snapshot}"
    )

    ai_response = _ask_ollama_page_analysis(snapshot, prompt)
    if not ai_response:
        return None, None

    selector = ai_response.strip().strip('"').strip("'").strip("`").strip()
    if not selector or len(selector) < 3:
        return None, None

    logging.info(f"AI suggested selector for [{purpose}]: '{selector}'")

    sel_type = "xpath" if selector.startswith("//") or selector.startswith("(//") else "css"
    try:
        by = By.XPATH if sel_type == "xpath" else By.CSS_SELECTOR
        elements = driver.find_elements(by, selector)
        for el in elements:
            if el.is_displayed():
                _save_learned_selector(purpose, sel_type, selector)
                logging.info(f"AI selector worked for [{purpose}]: {sel_type}='{selector}'")
                return el, selector
    except Exception as e:
        logging.debug(f"AI selector '{selector}' failed: {e}")

    return None, None


def _ai_identify_page_state(driver, root_element=None):
    """Ask AI to classify what's currently on screen.
    Returns one of: 'chatbot', 'form', 'success', 'error', 'already_applied', 'unknown'."""
    snapshot = _extract_dom_snapshot(driver, root_element, max_chars=2000)
    if not snapshot:
        return "unknown"

    result = _ask_ollama_page_analysis(
        snapshot,
        "Classify what this webpage section shows. Return ONLY one word from this list: "
        "chatbot, form, success, error, already_applied, unknown. "
        "chatbot = a conversational chat interface with messages and a text/option input. "
        "form = a traditional form with labeled fields and inputs. "
        "success = a confirmation that application was submitted. "
        "error = an error message. "
        "already_applied = message saying user already applied."
    )

    result = result.strip().lower().rstrip(".")
    valid = {"chatbot", "form", "success", "error", "already_applied", "unknown"}
    return result if result in valid else "unknown"


# ---------------------------------------------------------------------------
#  NAUKRI CHATBOT DETECTION  (verified selectors + learned + AI fallback)
# ---------------------------------------------------------------------------

def _detect_naukri_chatbot(driver):
    """Detect Naukri's chatbot questionnaire.

    Detection chain (each step falls through to the next):
      1. Self-learning cache (selectors that worked before)
      2. Verified known selectors (chatList_, textArea, ssrc__radio)
      3. Placeholder-based input detection
      4. AI-powered DOM analysis (asks Ollama to find the chatbot)

    Returns dict with keys: chat_list, text_input, has_radios, container
    or None if no chatbot is found.
    """
    result = {"chat_list": None, "text_input": None, "has_radios": False, "container": None}

    # --- Step 0: Try learned selectors from previous runs ---
    learned_cl = _try_learned_selectors(driver, "chat_list")
    if learned_cl:
        result["chat_list"] = learned_cl

    learned_ti = _try_learned_selectors(driver, "text_input")
    if learned_ti:
        result["text_input"] = learned_ti

    # --- Step 1: Known chat list selector ---
    if not result["chat_list"]:
        try:
            chat_lists = driver.find_elements(By.CSS_SELECTOR, "ul[id*='chatList_']")
            for cl in chat_lists:
                if cl.is_displayed():
                    result["chat_list"] = cl
                    _save_learned_selector("chat_list", "css", f"ul#{cl.get_attribute('id')}")
                    logging.info(f"Found Naukri chat list: id='{cl.get_attribute('id')}'")
                    break
        except Exception:
            pass

    # --- Step 2: Known text input selectors ---
    if not result["text_input"]:
        text_area_selectors = [
            "div.textArea",
            "[class*='textArea']",
            "div[contenteditable='true']",
        ]
        for sel in text_area_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in elements:
                    if el.is_displayed():
                        result["text_input"] = el
                        _save_learned_selector("text_input", "css", sel)
                        logging.info(f"Found Naukri text input: {sel}")
                        break
                if result["text_input"]:
                    break
            except Exception:
                continue

    # --- Step 3: Placeholder-based input detection ---
    if not result["text_input"]:
        try:
            all_inputs = driver.find_elements(By.CSS_SELECTOR,
                "input[type='text'], input:not([type]), textarea")
            for inp in all_inputs:
                if not inp.is_displayed():
                    continue
                placeholder = (inp.get_attribute("placeholder") or "").lower()
                if any(kw in placeholder for kw in [
                    "type message", "type your", "enter your answer",
                    "type here", "your answer", "write here",
                ]):
                    result["text_input"] = inp
                    logging.info(f"Found chatbot input via placeholder: '{placeholder}'")
                    break
        except Exception:
            pass

    # --- Step 4: Known radio button selectors ---
    try:
        radios = driver.find_elements(By.CSS_SELECTOR, ".ssrc__radio-btn-container")
        visible_radios = [r for r in radios if r.is_displayed()]
        if visible_radios:
            result["has_radios"] = True
            logging.info(f"Found {len(visible_radios)} Naukri radio button containers")
    except Exception:
        pass

    # --- Step 5: AI fallback if nothing found yet ---
    if not result["chat_list"] and not result["text_input"] and not result["has_radios"]:
        page_state = _ai_identify_page_state(driver)
        logging.info(f"AI page state classification: '{page_state}'")

        if page_state in ("chatbot", "form"):
            ai_input, ai_sel = _ai_find_element(
                driver, None, "text_input",
                "the text input field where the applicant types their answer to screening questions "
                "(could be a div with contenteditable, an input with placeholder about typing message, "
                "or a textarea)"
            )
            if ai_input:
                result["text_input"] = ai_input

            if not result["chat_list"]:
                ai_cl, ai_cl_sel = _ai_find_element(
                    driver, None, "chat_list",
                    "the chat message list container (usually a ul or div) that holds "
                    "the conversation messages between the bot and the applicant"
                )
                if ai_cl:
                    result["chat_list"] = ai_cl
        else:
            return None

    if not result["chat_list"] and not result["text_input"] and not result["has_radios"]:
        return None

    # --- Find the outermost drawer/container ---
    anchor = result["chat_list"] or result["text_input"]
    if anchor:
        try:
            result["container"] = driver.execute_script("""
                var el = arguments[0];
                for (var i = 0; i < 15; i++) {
                    el = el.parentElement;
                    if (!el) return document.body;
                    var cls = (el.className || '').toString().toLowerCase();
                    if (cls.indexOf('chatbot') >= 0 || cls.indexOf('drawer') >= 0
                        || cls.indexOf('screening') >= 0 || cls.indexOf('dialog') >= 0
                        || cls.indexOf('modal') >= 0 || cls.indexOf('overlay') >= 0) {
                        return el;
                    }
                }
                return el || document.body;
            """, anchor)
        except Exception:
            result["container"] = driver.find_element(By.TAG_NAME, "body")
    else:
        result["container"] = driver.find_element(By.TAG_NAME, "body")

    logging.info("Naukri chatbot detected successfully")
    return result


def _get_chatbot_question_text(driver, chatbot_info, previous_questions):
    """Extract the latest unanswered bot question from the Naukri chatbot.

    Uses verified selectors:
      - li[class*='botItem'] for bot message items
      - The span inside for the actual question text
      - Falls back to parsing ul[id*='chatList_'] li elements
      - Ultimate fallback: AI analysis of page text
    """

    # --- Strategy 1: Direct bot item selector ---
    bot_item_selectors = [
        "li[class*='botItem'] div div span",
        "li[class*='botItem'] span",
        "li.botItem.chatbot_ListItem div div span",
        "li.botItem span",
        "li[class*='botItem']",
    ]

    search_root = chatbot_info.get("chat_list") or chatbot_info.get("container") or driver

    for sel in bot_item_selectors:
        try:
            elements = search_root.find_elements(By.CSS_SELECTOR, sel)
            for el in reversed(elements):
                if not el.is_displayed():
                    continue
                text = el.text.strip()
                if not text or len(text) < 5 or text in previous_questions:
                    continue
                lower = text.lower()
                if any(skip in lower for skip in [
                    "thank you for showing interest",
                    "kindly answer all",
                    "hi ", "hello ",
                ]):
                    if "?" not in text:
                        continue
                if "successfully applied" in lower or "application submitted" in lower:
                    return None
                logging.info(f"Bot question (selector '{sel}'): '{text[:100]}'")
                return text
        except Exception:
            continue

    # --- Strategy 2: Parse chat list li elements ---
    chat_list = chatbot_info.get("chat_list")
    if chat_list:
        try:
            li_elements = chat_list.find_elements(By.TAG_NAME, "li")
            for li in reversed(li_elements):
                if not li.is_displayed():
                    continue
                li_class = (li.get_attribute("class") or "").lower()
                if "useritm" in li_class or "useritem" in li_class or "user" in li_class.split():
                    continue
                text = li.text.strip()
                if not text or len(text) < 5 or text in previous_questions:
                    continue
                lower = text.lower()
                if "thank you for showing" in lower and "?" not in text:
                    continue
                if "kindly answer" in lower and "?" not in text:
                    continue
                if "successfully applied" in lower:
                    return None
                logging.info(f"Bot question (chatList li): '{text[:100]}'")
                return text
        except Exception as e:
            logging.debug(f"Chat list parsing error: {e}")

    # --- Strategy 3: Broad page text scan for questions ---
    container = chatbot_info.get("container")
    if container:
        try:
            page_text = container.text
            lines = [ln.strip() for ln in page_text.split("\n") if ln.strip()]
            for line in reversed(lines):
                if line in previous_questions or len(line) < 10:
                    continue
                lower = line.lower()
                if "thank you for showing" in lower or "kindly answer" in lower:
                    continue
                if "successfully applied" in lower:
                    return None
                if "?" in line or any(kw in lower for kw in [
                    "experience", "ctc", "salary", "notice period", "relocat",
                    "willing", "proficien", "certif", "skill", "years",
                    "current", "expected", "available", "start date",
                    "date of birth", "gender", "qualification",
                ]):
                    logging.info(f"Bot question (text scan): '{line[:100]}'")
                    return line
        except Exception:
            pass

    # --- Strategy 4: AI-powered page context analysis ---
    if container:
        try:
            visible_text = container.text[:3000]
            if visible_text.strip():
                ai_result = _ask_ollama_page_analysis(
                    visible_text,
                    "This is text from a job application chatbot. "
                    "Extract ONLY the latest question being asked to the applicant. "
                    "Ignore greetings. Return ONLY the question text, nothing else. "
                    f"Already answered questions to skip: {list(previous_questions)[:5]}"
                )
                if ai_result and len(ai_result) > 5 and ai_result not in previous_questions:
                    ai_result = ai_result.strip('"').strip("'").strip()
                    logging.info(f"Bot question (AI analysis): '{ai_result[:100]}'")
                    return ai_result
        except Exception as e:
            logging.debug(f"AI page analysis failed: {e}")

    return None


def _click_naukri_save_button(driver, chatbot_info):
    """Click the Save/Submit button in Naukri's chatbot questionnaire.

    Strategy chain:
    0. Learned selectors from previous runs
    1. Known absolute XPath from verified repos
    2. CSS selectors for common button patterns
    3. Broad text-based search
    4. AI-powered DOM analysis to locate the button
    """

    # Strategy 0: Learned selectors
    learned_btn = _try_learned_selectors(driver, "save_button")
    if learned_btn:
        try:
            driver.execute_script("arguments[0].click();", learned_btn)
            logging.info("Clicked save button via learned selector")
            return True
        except Exception:
            pass

    # Strategy 1: Known Naukri save button XPaths
    naukri_save_xpaths = [
        "/html/body/div[2]/div/div[1]/div[3]/div/div",
        "/html/body/div[2]/div/div[1]/div[3]/div",
        "//div[contains(@class, 'chatbot')]//div[contains(@class, 'save')]",
        "//div[contains(@class, 'chatbot')]//button",
    ]

    for xpath in naukri_save_xpaths:
        try:
            elements = driver.find_elements(By.XPATH, xpath)
            for el in elements:
                if el.is_displayed() and el.size.get("height", 0) > 10:
                    driver.execute_script("arguments[0].click();", el)
                    _save_learned_selector("save_button", "xpath", xpath)
                    logging.info(f"Clicked save button via XPath: {xpath}")
                    return True
        except Exception:
            continue

    # Strategy 2: CSS selectors
    save_css_selectors = [
        "[class*='save']",
        "[class*='Save']",
        "[class*='submit']",
        "[class*='Submit']",
        "[class*='send']",
        "[class*='Send']",
        "button[type='submit']",
        "input[type='submit']",
    ]

    container = chatbot_info.get("container") or driver
    for sel in save_css_selectors:
        try:
            buttons = container.find_elements(By.CSS_SELECTOR, sel)
            for btn in buttons:
                if btn.is_displayed():
                    btn_text = (btn.text or "").strip().lower()
                    if btn_text in ("", "save", "submit", "send", "next", "continue", "done"):
                        driver.execute_script("arguments[0].click();", btn)
                        _save_learned_selector("save_button", "css", sel)
                        logging.info(f"Clicked save button via CSS: {sel} (text: '{btn.text}')")
                        return True
        except Exception:
            continue

    # Strategy 3: All visible buttons with matching text
    try:
        all_buttons = driver.find_elements(By.XPATH,
            "//button | //div[contains(@class,'btn')] | //a[contains(@class,'btn')] | //input[@type='submit']")
        for btn in all_buttons:
            if not btn.is_displayed():
                continue
            btn_text = (btn.text or "").strip().lower()
            if btn_text in ("save", "submit", "send", "next", "continue", "done", "apply"):
                driver.execute_script("arguments[0].click();", btn)
                logging.info(f"Clicked save button (broad): text='{btn.text}'")
                return True
    except Exception:
        pass

    # Strategy 4: AI fallback
    logging.info("Standard selectors failed for save button, asking AI...")
    ai_btn, ai_sel = _ai_find_element(
        driver, container, "save_button",
        "the Save or Submit button that the user clicks after answering a chatbot question "
        "in a job application questionnaire. It might be a div, button, or clickable element "
        "with text like Save, Submit, Send, Next, or even no text."
    )
    if ai_btn:
        try:
            driver.execute_script("arguments[0].click();", ai_btn)
            logging.info(f"Clicked save button via AI: '{ai_sel}'")
            return True
        except Exception as e:
            logging.warning(f"AI-found save button click failed: {e}")

    logging.warning("Could not find save/submit button (all strategies exhausted)")
    return False


def _check_application_success(driver, use_ai=False):
    """Check if the application was successfully submitted.
    Uses Naukri's known success indicators first, then AI as fallback."""
    success_selectors = [
        ("css", "span[class*='apply-message']"),
        ("css", "div[class*='apply-status-header']"),
        ("css", "[class*='successfull']"),
        ("css", "[class*='success-message']"),
        ("xpath", "//span[contains(text(), 'successfully applied')]"),
        ("xpath", "//span[contains(text(), 'Successfully applied')]"),
        ("xpath", "//div[contains(@class, 'apply-status-header') and contains(@class, 'green')]"),
    ]

    for method, selector in success_selectors:
        try:
            if method == "css":
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
            else:
                elements = driver.find_elements(By.XPATH, selector)
            for el in elements:
                if el.is_displayed():
                    text = el.text.strip().lower()
                    if "successfully applied" in text or "green" in (el.get_attribute("class") or ""):
                        return True
        except Exception:
            continue

    try:
        page_source = driver.page_source.lower()
        if "successfully applied" in page_source or "application submitted" in page_source:
            return True
    except Exception:
        pass

    if use_ai:
        page_state = _ai_identify_page_state(driver)
        if page_state == "success":
            logging.info("AI confirmed application success")
            return True

    return False


def _find_clickable_option_buttons(driver, answer_text):
    """Find and click option buttons (city selection, skill chips, etc.) when the
    chatbot replaces the text input with clickable options.

    Naukri's chatbot sometimes shows clickable option buttons/chips instead of a
    text input for questions like 'select the city'. These aren't radio buttons
    (.ssrc__radio-btn-container) but standalone clickable elements inside the chat.

    Returns True if an option was clicked, False otherwise."""

    answer_lower = (answer_text or "").strip().lower()
    if not answer_lower:
        return False

    option_selectors = [
        "li[class*='botItem'] button",
        "li[class*='botItem'] div[class*='option']",
        "li[class*='botItem'] span[class*='option']",
        "li[class*='botItem'] a[class*='option']",
        "[class*='chatOption']",
        "[class*='suggestedOption']",
        "[class*='chip']",
        "[class*='Chip']",
        "[class*='optionItem']",
        "[class*='OptionItem']",
        "[class*='option-btn']",
        "[class*='optionBtn']",
        "[class*='select-option']",
        "[class*='multi-select'] li",
        "[class*='multiSelect'] li",
        "[class*='dropdown-option']",
        "ul[class*='option'] li",
        "div[class*='option'] span",
    ]

    all_options = []
    for sel in option_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in elements:
                if el.is_displayed() and el.text.strip():
                    all_options.append(el)
        except Exception:
            continue

    if not all_options:
        try:
            clickables = driver.find_elements(By.XPATH,
                "//div[contains(@class,'chatbot') or contains(@class,'chat')]"
                "//button | //div[contains(@class,'chatbot') or contains(@class,'chat')]"
                "//div[@role='option'] | //div[contains(@class,'chatbot') or "
                "contains(@class,'chat')]//li[@role='option']")
            for el in clickables:
                if el.is_displayed() and el.text.strip():
                    all_options.append(el)
        except Exception:
            pass

    if not all_options:
        return False

    option_texts = [el.text.strip() for el in all_options]
    logging.info(f"Found {len(all_options)} clickable option buttons: {option_texts[:10]}")

    matched_text = _fuzzy_match_option(answer_text, option_texts)
    if matched_text:
        for el in all_options:
            if el.text.strip().lower() == matched_text.lower():
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
                    time.sleep(random.uniform(0.3, 0.6))
                    driver.execute_script("arguments[0].click();", el)
                    logging.info(f"Clicked option button: '{el.text.strip()}'")
                    return True
                except Exception as e:
                    logging.warning(f"Failed to click option '{el.text.strip()}': {e}")
                    continue

    for el in all_options:
        el_text = el.text.strip().lower()
        if answer_lower in el_text or el_text in answer_lower:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
                time.sleep(random.uniform(0.3, 0.6))
                driver.execute_script("arguments[0].click();", el)
                logging.info(f"Clicked option button (substring match): '{el.text.strip()}'")
                return True
            except Exception:
                continue

    return False


def _ai_find_and_click_option(driver, answer_text, question_text):
    """Use AI to find clickable option elements when standard selectors fail.
    Extracts a DOM snapshot, asks Ollama for a selector, tries to click."""

    snapshot = _extract_dom_snapshot(driver)
    if not snapshot:
        return False

    prompt = (
        f"The user is answering the question: '{question_text}'\n"
        f"Their answer is: '{answer_text}'\n\n"
        "The chatbot shows clickable options/buttons instead of a text input.\n"
        "Find the CSS selector or XPath for the clickable option that best "
        f"matches '{answer_text}'. Return ONLY one selector line like:\n"
        "css: <selector>\nor\nxpath: <selector>\n\n"
        f"Page DOM:\n{snapshot[:3000]}"
    )

    response = _ask_ollama_page_analysis(snapshot, prompt)
    if not response:
        return False

    for line in response.strip().split("\n"):
        line = line.strip()
        if line.startswith("css:"):
            sel = line[4:].strip().strip("'\"")
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    if el.is_displayed():
                        driver.execute_script("arguments[0].click();", el)
                        logging.info(f"AI clicked option via CSS: {sel}")
                        return True
            except Exception:
                pass
        elif line.startswith("xpath:"):
            sel = line[6:].strip().strip("'\"")
            try:
                els = driver.find_elements(By.XPATH, sel)
                for el in els:
                    if el.is_displayed():
                        driver.execute_script("arguments[0].click();", el)
                        logging.info(f"AI clicked option via XPath: {sel}")
                        return True
            except Exception:
                pass

    return False


def _wait_for_dom_settle(driver, wait_range=(1.5, 3.0)):
    """Wait for DOM to settle after a save/submit action.
    Gives the chatbot time to process the answer and render the next question."""
    time.sleep(random.uniform(*wait_range))


def _detect_visible_input_type(driver):
    """Scan the DOM to determine what input type is currently visible.

    Returns one of: 'radio', 'checkbox', 'date', 'text', 'options', or None.
    Also returns any relevant elements found as a list.
    """
    try:
        radios = driver.find_elements(By.CSS_SELECTOR, ".ssrc__radio-btn-container")
        visible_radios = [r for r in radios if r.is_displayed()]
        if visible_radios:
            return "radio", visible_radios
    except Exception:
        pass

    try:
        checkboxes = driver.find_elements(By.CSS_SELECTOR,
            ".ssrc__checkbox-container, "
            "[class*='checkbox-container'], "
            "[class*='checkboxContainer'], "
            "li input[type='checkbox'], "
            "div input[type='checkbox']")
        visible_cbs = []
        for cb in checkboxes:
            try:
                if cb.is_displayed():
                    visible_cbs.append(cb)
            except Exception:
                continue
        if visible_cbs:
            return "checkbox", visible_cbs

        cb_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
        visible_cb_inputs = [c for c in cb_inputs if c.is_displayed()]
        if visible_cb_inputs:
            return "checkbox", visible_cb_inputs
    except Exception:
        pass

    try:
        date_selectors = [
            "ul[id*='dob__input-container']",
            "input[type='date']",
            "input[placeholder*='DD']",
            "input[placeholder*='dd/mm']",
            "input[placeholder*='MM/DD']",
            "[class*='datePicker']",
            "[class*='date-picker']",
        ]
        for sel in date_selectors:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            visible = [e for e in els if e.is_displayed()]
            if visible:
                return "date", visible
    except Exception:
        pass

    try:
        option_selectors = [
            "li[class*='botItem'] button",
            "li[class*='botItem'] div[class*='option']",
            "[class*='chatOption']",
            "[class*='suggestedOption']",
            "[class*='chip']",
            "[class*='Chip']",
            "[class*='optionItem']",
            "[class*='OptionItem']",
            "[class*='option-btn']",
            "[class*='optionBtn']",
            "[class*='select-option']",
            "[class*='multi-select'] li",
            "[class*='multiSelect'] li",
            "[class*='dropdown-option']",
            "ul[class*='option'] li",
            "div[class*='option'] span",
            "div[role='option']",
            "li[role='option']",
            "button[class*='option']",
            "[class*='ssrc__option']",
        ]
        visible_opts = []
        for sel in option_selectors:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    if el.is_displayed() and el.text.strip():
                        visible_opts.append(el)
            except Exception:
                continue
        if len(visible_opts) >= 2:
            return "options", visible_opts
    except Exception:
        pass

    try:
        text_selectors = [
            "div.textArea",
            "[class*='textArea']",
            "div[contenteditable='true']",
        ]
        for sel in text_selectors:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            visible = [e for e in els if e.is_displayed()]
            if visible:
                return "text", visible
    except Exception:
        pass

    return None, []


def _handle_checkbox_question(driver, chatbot_info, question_text, answer, job_title, company, source, config_key):
    """Handle checkbox-type questions (e.g., city multi-select).

    Finds visible checkboxes, extracts their labels, matches the answer,
    and clicks the matching checkbox(es). Returns True if handled.
    """
    checkbox_selectors = [
        ".ssrc__checkbox-container",
        "[class*='checkbox-container']",
        "[class*='checkboxContainer']",
    ]

    containers = []
    for sel in checkbox_selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            containers.extend([e for e in els if e.is_displayed()])
        except Exception:
            continue

    options = []
    elements = []

    if containers:
        for cont in containers:
            try:
                label = cont.find_element(By.CSS_SELECTOR, "label")
                label_text = label.text.strip()
                if label_text:
                    options.append(label_text)
                    elements.append(cont)
            except Exception:
                try:
                    span = cont.find_element(By.CSS_SELECTOR, "span")
                    span_text = span.text.strip()
                    if span_text:
                        options.append(span_text)
                        elements.append(cont)
                except Exception:
                    text = cont.text.strip()
                    if text:
                        options.append(text)
                        elements.append(cont)

    if not options:
        try:
            cb_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
            for cb in cb_inputs:
                if not cb.is_displayed():
                    continue
                label_text = ""
                try:
                    cb_id = cb.get_attribute("id")
                    if cb_id:
                        labels = driver.find_elements(By.CSS_SELECTOR, f"label[for='{cb_id}']")
                        if labels:
                            label_text = labels[0].text.strip()
                except Exception:
                    pass
                if not label_text:
                    try:
                        parent = cb.find_element(By.XPATH, "./..")
                        label_text = parent.text.strip()
                    except Exception:
                        label_text = cb.get_attribute("value") or ""
                if label_text:
                    options.append(label_text)
                    elements.append(cb)
        except Exception:
            pass

    if not options:
        logging.info("No checkbox options found")
        return False

    logging.info(f"Checkbox options: {options}")

    matched = _fuzzy_match_option(answer, options)
    clicked = False

    for i, opt_text in enumerate(options):
        if matched and opt_text.lower().strip() == matched.lower().strip():
            el = elements[i]
            try:
                cb_input = el.find_element(By.CSS_SELECTOR, "input[type='checkbox']")
                driver.execute_script("arguments[0].click();", cb_input)
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            logging.info(f"Selected checkbox: '{opt_text}'")
            clicked = True
            break

    if not clicked:
        answer_lower = answer.lower().strip()
        for i, opt_text in enumerate(options):
            if answer_lower in opt_text.lower() or opt_text.lower() in answer_lower:
                el = elements[i]
                try:
                    cb_input = el.find_element(By.CSS_SELECTOR, "input[type='checkbox']")
                    driver.execute_script("arguments[0].click();", cb_input)
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                logging.info(f"Selected checkbox (substring): '{opt_text}'")
                clicked = True
                break

    if not clicked and elements:
        el = elements[0]
        try:
            cb_input = el.find_element(By.CSS_SELECTOR, "input[type='checkbox']")
            driver.execute_script("arguments[0].click();", cb_input)
        except Exception:
            driver.execute_script("arguments[0].click();", el)
        logging.info(f"Selected first checkbox as fallback: '{options[0]}'")
        clicked = True

    if clicked:
        time.sleep(random.uniform(0.8, 1.5))
        _click_naukri_save_button(driver, chatbot_info)
        log_qa(job_title, company, question_text, "checkbox", str(matched or answer), source, config_key)

    return clicked


def _handle_date_question(driver, chatbot_info, question_text, job_title, company, resume_context):
    """Handle date-of-birth / date questions with multiple selector strategies.

    Reads DOB from: .env DATE_OF_BIRTH -> screening config -> Ollama -> fallback.
    Tries multiple input patterns: dedicated DOB container, date input, text input.
    Returns True if handled.
    """
    env_dob = os.getenv("DATE_OF_BIRTH", "").strip()
    answer = env_dob if env_dob else None
    source = "env" if answer else None
    config_key = None

    if not answer:
        answer, config_key, _ = match_config(question_text)
        source = "config" if answer else None

    if not answer:
        answer = ask_ollama(question_text, [], resume_context)
        source = "ollama" if answer else None

    if not answer:
        answer = "01/01/1995"
        source = "fallback"

    date_input_selectors = [
        "ul[id*='dob__input-container']",
        "input[type='date']",
        "input[placeholder*='DD']",
        "input[placeholder*='dd/mm']",
        "input[placeholder*='MM/DD']",
        "input[placeholder*='YYYY']",
        "[class*='datePicker'] input",
        "[class*='date-picker'] input",
        "[class*='dateInput'] input",
    ]

    for sel in date_input_selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                if el.is_displayed():
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
                    el.click()
                    time.sleep(random.uniform(0.3, 0.6))
                    el.send_keys(str(answer))
                    time.sleep(random.uniform(0.5, 1.0))
                    _click_naukri_save_button(driver, chatbot_info)
                    log_qa(job_title, company, question_text, "date", answer, source, config_key)
                    logging.info(f"Entered date: '{answer}' via {sel}")
                    return True
        except Exception:
            continue

    return False


def _type_into_text_input(driver, chatbot_info, answer):
    """Try to type an answer into the chatbot text input. Returns True on success."""

    text_input = chatbot_info.get("text_input")

    if text_input:
        try:
            if not text_input.is_displayed():
                raise StaleElementReferenceException("Text input no longer visible")

            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", text_input)
            text_input.click()
            time.sleep(random.uniform(0.3, 0.7))

            tag = text_input.tag_name.lower()
            is_contenteditable = text_input.get_attribute("contenteditable") == "true"

            if is_contenteditable or tag == "div":
                driver.execute_script("arguments[0].textContent = '';", text_input)
            else:
                text_input.clear()

            time.sleep(random.uniform(0.2, 0.5))

            for char in str(answer):
                text_input.send_keys(char)
                time.sleep(random.uniform(0.02, 0.06))

            logging.info(f"Typed answer: '{answer}'")
            return True

        except (StaleElementReferenceException, NoSuchElementException):
            logging.warning("Text input became stale, re-detecting...")
            chatbot_info["text_input"] = None
        except Exception as e:
            logging.error(f"Error typing answer: {e}")
            chatbot_info["text_input"] = None

    for attempt in range(2):
        if attempt > 0:
            time.sleep(random.uniform(1.0, 2.0))
        new_input = _refind_text_input(driver)
        if new_input:
            chatbot_info["text_input"] = new_input
            try:
                new_input.click()
                time.sleep(random.uniform(0.2, 0.4))
                new_input.send_keys(str(answer))
                logging.info(f"Typed answer via re-detected input: '{answer}'")
                return True
            except Exception as e:
                logging.error(f"Re-detected text input failed (attempt {attempt+1}): {e}")

    return False


def _handle_naukri_chatbot(driver, chatbot_info, job_title, company, resume_context):
    """Handle Naukri's chatbot questionnaire using detect-first approach.

    Each iteration:
      1. Detect what input type is currently visible (radio/checkbox/date/text)
      2. Get the question text
      3. Get an answer from config -> Ollama -> fallback
      4. Interact with the detected input type
      5. Click save

    Falls back to clickable option scanning and AI if detection finds nothing.
    """

    max_iterations = 20
    answered_count = 0
    previous_questions = set()
    consecutive_failures = 0

    for iteration in range(max_iterations):
        _wait_for_dom_settle(driver, (1.5, 3.5))

        if _check_application_success(driver):
            logging.info("Application submitted successfully (detected success message)")
            save_screenshot(driver, f"chatbot_success_{company.replace(' ', '_')[:20]}", "success")
            return True

        # --- Step 1: Detect visible input type ---
        input_type, input_elements = _detect_visible_input_type(driver)
        if input_type:
            logging.debug(f"Detected input type: {input_type} ({len(input_elements)} elements)")

        # --- Step 2: Get the question text ---
        question_text = _get_chatbot_question_text(driver, chatbot_info, previous_questions)

        if not question_text:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                logging.info("No more questions detected, finishing chatbot")
                break
            continue

        consecutive_failures = 0
        previous_questions.add(question_text)
        question_lower = question_text.lower()
        logging.info(f"Chatbot Q{iteration+1} [{input_type or 'unknown'}]: '{question_text[:100]}'")

        # --- Step 3: Handle by detected input type ---
        handled = False

        # --- RADIO ---
        if input_type == "radio" and input_elements:
            options = []
            for rc in input_elements:
                try:
                    label = rc.find_element(By.CSS_SELECTOR, "label")
                    options.append(label.text.strip())
                except Exception:
                    try:
                        inp = rc.find_element(By.CSS_SELECTOR, "input")
                        options.append(inp.get_attribute("value") or "")
                    except Exception:
                        pass

            logging.info(f"Radio options: {options}")

            answer, config_key, _ = match_config(question_text)
            source = "config"
            if answer is None:
                source = "ollama"
                config_key = None
                answer = ask_ollama(question_text, options, resume_context)
            if not answer and options:
                answer = options[0]
                source = "fallback"

            matched = _fuzzy_match_option(answer, options) if options else answer
            clicked = False
            for i, rc in enumerate(input_elements):
                label_text = options[i] if i < len(options) else ""
                if matched and label_text.lower().strip() == matched.lower().strip():
                    try:
                        radio_input = rc.find_element(By.CSS_SELECTOR, "input")
                        driver.execute_script("arguments[0].click();", radio_input)
                    except Exception:
                        driver.execute_script("arguments[0].click();", rc)
                    logging.info(f"Selected radio: '{label_text}'")
                    clicked = True
                    break

            if not clicked and input_elements:
                try:
                    first_input = input_elements[0].find_element(By.CSS_SELECTOR, "input")
                    driver.execute_script("arguments[0].click();", first_input)
                except Exception:
                    driver.execute_script("arguments[0].click();", input_elements[0])
                logging.info(f"Selected first radio as fallback: '{options[0] if options else 'N/A'}'")
                clicked = True

            if clicked:
                time.sleep(random.uniform(0.8, 1.5))
                _click_naukri_save_button(driver, chatbot_info)
                answered_count += 1
                handled = True

            log_qa(job_title, company, question_text, "radio", str(matched or answer), source, config_key)

        # --- CHECKBOX ---
        elif input_type == "checkbox":
            answer, config_key, _ = match_config(question_text)
            source = "config"
            if answer is None:
                source = "ollama"
                config_key = None
                answer = ask_ollama(question_text, [], resume_context)
            if not answer:
                answer = "N/A"
                source = "fallback"

            handled = _handle_checkbox_question(
                driver, chatbot_info, question_text, answer,
                job_title, company, source, config_key
            )
            if handled:
                answered_count += 1

        # --- DATE ---
        elif input_type == "date" or "date of birth" in question_lower or "dob" in question_lower or "birthday" in question_lower:
            handled = _handle_date_question(
                driver, chatbot_info, question_text,
                job_title, company, resume_context
            )
            if handled:
                answered_count += 1

        # --- CLICKABLE OPTIONS (city chips, skill pills, etc.) ---
        elif input_type == "options" and input_elements:
            answer, config_key, _ = match_config(question_text)
            source = "config"
            if answer is None:
                source = "ollama"
                config_key = None
                option_texts = [el.text.strip() for el in input_elements if el.text.strip()]
                answer = ask_ollama(question_text, option_texts, resume_context)
            if not answer:
                answer = input_elements[0].text.strip() if input_elements else "N/A"
                source = "fallback"

            clicked_option = _find_clickable_option_buttons(driver, answer)

            if not clicked_option:
                answer_lower_opt = answer.lower().strip()
                for el in input_elements:
                    el_text = el.text.strip().lower()
                    if answer_lower_opt in el_text or el_text in answer_lower_opt:
                        try:
                            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
                            time.sleep(random.uniform(0.3, 0.6))
                            driver.execute_script("arguments[0].click();", el)
                            logging.info(f"Clicked detected option element: '{el.text.strip()}'")
                            clicked_option = True
                            break
                        except Exception:
                            continue

            if not clicked_option:
                clicked_option = _ai_find_and_click_option(driver, answer, question_text)

            if clicked_option:
                time.sleep(random.uniform(0.8, 1.5))
                save_needed = True
                try:
                    if _check_application_success(driver):
                        save_needed = False
                except Exception:
                    pass
                if save_needed:
                    _click_naukri_save_button(driver, chatbot_info)
                answered_count += 1
                handled = True
                log_qa(job_title, company, question_text, "chatbot_option_click", answer, source, config_key)

        # --- TEXT ---
        if not handled:
            answer, config_key, _ = match_config(question_text)
            source = "config"
            if answer is None:
                source = "ollama"
                config_key = None
                answer = ask_ollama(question_text, [], resume_context)
            if not answer:
                answer = "N/A"
                source = "fallback"
                logging.warning(f"No answer for: '{question_text[:80]}', using fallback")

            typed = _type_into_text_input(driver, chatbot_info, answer)

            if not typed:
                logging.info("Text input not available, checking for clickable option buttons...")
                clicked_option = _find_clickable_option_buttons(driver, answer)

                if not clicked_option:
                    logging.info("Standard option selectors failed, trying AI...")
                    clicked_option = _ai_find_and_click_option(driver, answer, question_text)

                if clicked_option:
                    time.sleep(random.uniform(0.8, 1.5))
                    save_needed = True
                    try:
                        if _check_application_success(driver):
                            save_needed = False
                    except Exception:
                        pass
                    if save_needed:
                        _click_naukri_save_button(driver, chatbot_info)
                    answered_count += 1
                    log_qa(job_title, company, question_text, "chatbot_option_click", answer, source, config_key)
                    continue

            if not typed:
                logging.warning("All input methods failed, asking AI to identify page state...")
                page_state = _ai_identify_page_state(driver, chatbot_info.get("container"))
                if page_state == "success":
                    logging.info("AI detected success page despite no typing")
                    return True
                elif page_state in ("error", "already_applied"):
                    logging.info(f"AI detected page state: {page_state}, stopping")
                    return False
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    logging.info("Repeated failures, finishing chatbot")
                    break
                continue

            if typed:
                time.sleep(random.uniform(0.5, 1.0))
                _click_naukri_save_button(driver, chatbot_info)
                answered_count += 1
                _wait_for_dom_settle(driver, (1.0, 2.0))

            log_qa(job_title, company, question_text, "chatbot_text", answer, source, config_key)

    # Final success check
    time.sleep(random.uniform(2, 4))
    if _check_application_success(driver, use_ai=True):
        logging.info(f"Chatbot completed: answered {answered_count} questions, application successful")
        save_screenshot(driver, f"chatbot_success_{company.replace(' ', '_')[:20]}", "success")
        return True

    logging.info(f"Chatbot completed: answered {answered_count} questions")
    save_screenshot(driver, f"chatbot_completed_{company.replace(' ', '_')[:20]}", "info")
    return answered_count > 0


def _refind_text_input(driver):
    """Re-find the chatbot text input when the previous reference goes stale.
    Uses: learned cache -> known selectors -> placeholder scan -> AI fallback."""

    # Try learned selectors first
    learned = _try_learned_selectors(driver, "text_input")
    if learned:
        return learned

    # Try Naukri's div.textArea
    for sel in ["div.textArea", "[class*='textArea']", "div[contenteditable='true']"]:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in elements:
                if el.is_displayed():
                    _save_learned_selector("text_input", "css", sel)
                    logging.info(f"Re-found text input: {sel}")
                    return el
        except Exception:
            continue

    # Placeholder scan
    try:
        all_inputs = driver.find_elements(By.CSS_SELECTOR,
            "input[type='text'], input:not([type]), textarea")
        for inp in all_inputs:
            if not inp.is_displayed():
                continue
            placeholder = (inp.get_attribute("placeholder") or "").lower()
            if any(kw in placeholder for kw in ["type message", "type your", "type here", "your answer"]):
                logging.info(f"Re-found text input via placeholder: '{placeholder}'")
                return inp
    except Exception:
        pass

    # AI fallback
    ai_el, _ = _ai_find_element(
        driver, None, "text_input",
        "the text input field where the applicant should type their answer in a chatbot "
        "questionnaire. Could be a div with contenteditable, input, or textarea."
    )
    return ai_el


# ---------------------------------------------------------------------------
#  LEGACY FORM-BASED DETECTION (fallback for non-chatbot questionnaires)
# ---------------------------------------------------------------------------

def _detect_form_questionnaire(driver):
    """Detect a traditional form-based questionnaire sidebar/dialog.
    Returns the container element or None."""
    sidebar_selectors = [
        "[class*='questionnaire']",
        "[class*='screening']",
        "[class*='apply-dialog']",
        "[class*='ApplyForm']",
        "[class*='applyForm']",
        "[class*='apply_dialog']",
        "[class*='screeningQues']",
        "[class*='ScreeningQues']",
    ]

    for sel in sidebar_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in elements:
                if el.is_displayed() and el.size.get("height", 0) > 50:
                    logging.info(f"Detected form questionnaire with selector: {sel}")
                    return el
        except Exception:
            continue

    try:
        dialogs = driver.find_elements(By.CSS_SELECTOR, "[role='dialog'], .modal, .overlay")
        for dialog in dialogs:
            if not dialog.is_displayed():
                continue
            form_inputs = dialog.find_elements(By.CSS_SELECTOR,
                "input[type='text'], input[type='number'], input[type='radio'], "
                "select, textarea, input[type='checkbox']")
            if len(form_inputs) >= 1:
                logging.info("Detected form questionnaire inside dialog/modal")
                return dialog
    except Exception:
        pass

    try:
        all_forms = driver.find_elements(By.CSS_SELECTOR, "form")
        for form in all_forms:
            if not form.is_displayed():
                continue
            form_text = form.text.lower()
            if any(kw in form_text for kw in ["ctc", "notice period", "experience", "relocate", "salary"]):
                inputs = form.find_elements(By.CSS_SELECTOR, "input, select, textarea")
                if len(inputs) >= 1:
                    logging.info("Detected form questionnaire via keyword heuristic")
                    return form
    except Exception:
        pass

    return None


def _extract_form_questions(driver, container):
    """Extract all questions from a traditional form questionnaire.
    Returns list of dicts: {text, input_type, element, options[], parent}."""
    questions = []

    question_block_selectors = [
        "[class*='question']",
        "[class*='Question']",
        "[class*='ques-']",
        "[class*='screenQues']",
        "[class*='formGroup']",
        "[class*='form-group']",
        ".form-group",
        "label",
    ]

    question_blocks = []
    for sel in question_block_selectors:
        try:
            blocks = container.find_elements(By.CSS_SELECTOR, sel)
            visible = [b for b in blocks if b.is_displayed() and b.text.strip()]
            if visible:
                question_blocks = visible
                logging.info(f"Found {len(visible)} question blocks with: {sel}")
                break
        except Exception:
            continue

    if not question_blocks:
        question_blocks = [container]

    for block in question_blocks:
        try:
            if not block.is_displayed():
                continue

            question_text = ""
            try:
                label_els = block.find_elements(By.CSS_SELECTOR,
                    "label, [class*='label'], [class*='question-text'], [class*='msgText']")
                for lbl in label_els:
                    text = lbl.text.strip()
                    if text and len(text) > 3:
                        question_text = text
                        break
            except Exception:
                pass

            if not question_text:
                block_text = block.text.strip()
                lines = [ln.strip() for ln in block_text.split("\n") if ln.strip()]
                if lines:
                    question_text = lines[0]

            if not question_text or len(question_text) < 3:
                continue

            input_type = "unknown"
            input_element = None
            options = []

            for detect_fn in [
                lambda: _detect_radios(driver, block),
                lambda: _detect_select(block),
                lambda: _detect_checkboxes(driver, block),
                lambda: _detect_textarea(block),
                lambda: _detect_text_input(block),
                lambda: _detect_clickable_options(block),
            ]:
                result = detect_fn()
                if result:
                    input_type, input_element, options = result
                    break

            if input_element is None:
                continue

            questions.append({
                "text": question_text,
                "input_type": input_type,
                "element": input_element,
                "options": options,
                "parent": block,
            })
        except StaleElementReferenceException:
            continue
        except Exception as e:
            logging.debug(f"Error extracting question block: {e}")
            continue

    logging.info(f"Extracted {len(questions)} form questions")
    return questions


def _detect_radios(driver, block):
    try:
        radios = block.find_elements(By.CSS_SELECTOR, "input[type='radio']")
        if not radios:
            return None
        options = []
        for radio in radios:
            try:
                label = driver.execute_script(
                    "var id=arguments[0].id; if(id) return document.querySelector('label[for=\"'+id+'\"]'); "
                    "return arguments[0].parentElement;", radio)
                opt_text = label.text.strip() if label else radio.get_attribute("value") or ""
            except Exception:
                opt_text = radio.get_attribute("value") or ""
            if opt_text:
                options.append(opt_text)
        return ("radio", radios, options)
    except Exception:
        return None


def _detect_select(block):
    try:
        selects = block.find_elements(By.CSS_SELECTOR, "select")
        if not selects:
            return None
        sel = selects[0]
        opt_els = sel.find_elements(By.TAG_NAME, "option")
        options = [o.text.strip() for o in opt_els if o.text.strip() and o.get_attribute("value") != ""]
        return ("select", sel, options)
    except Exception:
        return None


def _detect_checkboxes(driver, block):
    try:
        checkboxes = block.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
        if not checkboxes:
            return None
        options = []
        for cb in checkboxes:
            try:
                label = driver.execute_script(
                    "var id=arguments[0].id; if(id) return document.querySelector('label[for=\"'+id+'\"]'); "
                    "return arguments[0].parentElement;", cb)
                opt_text = label.text.strip() if label else cb.get_attribute("value") or ""
            except Exception:
                opt_text = cb.get_attribute("value") or ""
            if opt_text:
                options.append(opt_text)
        return ("checkbox", checkboxes, options)
    except Exception:
        return None


def _detect_textarea(block):
    try:
        textareas = block.find_elements(By.CSS_SELECTOR, "textarea")
        if textareas and textareas[0].is_displayed():
            return ("textarea", textareas[0], [])
    except Exception:
        pass
    return None


def _detect_text_input(block):
    try:
        text_inputs = block.find_elements(By.CSS_SELECTOR,
            "input[type='text'], input[type='number'], input:not([type])")
        for ti in text_inputs:
            if ti.is_displayed():
                return (ti.get_attribute("type") or "text", ti, [])
    except Exception:
        pass
    return None


def _detect_clickable_options(block):
    try:
        clickable = block.find_elements(By.CSS_SELECTOR,
            "[class*='option'], [class*='chip'], [class*='choice'], button[class*='opt']")
        if clickable:
            options = [opt.text.strip() for opt in clickable if opt.text.strip()]
            return ("clickable_options", clickable, options)
    except Exception:
        pass
    return None


def _fill_form_answer(driver, question, answer):
    """Fill a single form-based question."""
    input_type = question["input_type"]
    element = question["element"]
    options = question["options"]

    try:
        if input_type in ("text", "number"):
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            element.clear()
            time.sleep(random.uniform(0.3, 0.8))
            element.send_keys(str(answer))
            return True

        elif input_type == "textarea":
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            element.clear()
            time.sleep(random.uniform(0.3, 0.8))
            element.send_keys(str(answer))
            return True

        elif input_type == "select":
            matched = _fuzzy_match_option(answer, options)
            if matched:
                opt_elements = element.find_elements(By.TAG_NAME, "option")
                for opt_el in opt_elements:
                    if opt_el.text.strip() == matched:
                        opt_el.click()
                        return True
                Select(element).select_by_visible_text(matched)
                return True
            return False

        elif input_type == "radio":
            matched = _fuzzy_match_option(answer, options)
            radios = element
            for i, radio in enumerate(radios):
                label = options[i] if i < len(options) else ""
                if label.lower().strip() == (matched or answer).lower().strip():
                    driver.execute_script("arguments[0].click();", radio)
                    return True
            if radios:
                driver.execute_script("arguments[0].click();", radios[0])
                return True
            return False

        elif input_type == "checkbox":
            answer_lower = answer.lower()
            for i, cb in enumerate(element):
                cb_label = options[i] if i < len(options) else ""
                if cb_label.lower() in answer_lower or answer_lower in cb_label.lower():
                    if not cb.is_selected():
                        driver.execute_script("arguments[0].click();", cb)
            return True

        elif input_type == "clickable_options":
            matched = _fuzzy_match_option(answer, options)
            for opt_el in element:
                if opt_el.text.strip().lower() == (matched or answer).lower().strip():
                    driver.execute_script("arguments[0].click();", opt_el)
                    return True
            if element:
                driver.execute_script("arguments[0].click();", element[0])
                return True
            return False

    except Exception as e:
        logging.error(f"Error filling form answer: {e}")
        return False

    return False


def _click_form_submit(driver, container):
    """Find and click the submit button in a form questionnaire."""
    submit_selectors = [
        "button[type='submit']",
        "[class*='submit']", "[class*='Submit']",
        "button[class*='save']", "button[class*='Save']",
        "button[class*='apply']", "button[class*='Apply']",
        "button[class*='next']", "button[class*='Next']",
        "input[type='submit']",
    ]

    keywords = ["submit", "save", "apply", "next", "send", "continue", "done"]

    for sel in submit_selectors:
        try:
            buttons = container.find_elements(By.CSS_SELECTOR, sel)
            for btn in buttons:
                if btn.is_displayed() and any(w in (btn.text or "").lower() for w in keywords):
                    driver.execute_script("arguments[0].click();", btn)
                    logging.info(f"Clicked form submit: '{btn.text.strip()}'")
                    return True
        except Exception:
            continue

    try:
        all_buttons = driver.find_elements(By.XPATH,
            "//button[contains(text(),'Submit') or contains(text(),'Save') or "
            "contains(text(),'Apply') or contains(text(),'Next') or "
            "contains(text(),'Send') or contains(text(),'Done')]")
        for btn in all_buttons:
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                logging.info(f"Clicked form submit (XPath): '{btn.text.strip()}'")
                return True
    except Exception:
        pass

    logging.warning("Could not find form submit button")
    return False


# ---------------------------------------------------------------------------
#  MAIN ENTRY POINT
# ---------------------------------------------------------------------------

def handle_questionnaire(driver, job_title, company):
    """Main entry point: detect and handle questionnaires after clicking Apply.

    Detection priority:
      1. Naukri chatbot (verified selectors: chatList_, botItem, textArea, ssrc__radio)
      2. Traditional form-based questionnaire (dialog/modal with inputs)

    Returns True if a questionnaire was detected and handled.
    """
    if os.getenv("ANSWER_QUESTIONNAIRE", "false").lower() != "true":
        return False

    resume_context = load_resume_text()

    # --- Try Naukri chatbot first (most common after clicking Apply) ---
    chatbot_info = _detect_naukri_chatbot(driver)
    if chatbot_info:
        logging.info(f"Naukri chatbot detected for '{job_title}' at {company}")
        save_screenshot(driver, f"chatbot_detected_{company.replace(' ', '_')[:20]}", "info")
        return _handle_naukri_chatbot(driver, chatbot_info, job_title, company, resume_context)

    # --- Fallback: traditional form questionnaire ---
    container = _detect_form_questionnaire(driver)
    if not container:
        logging.info("Standard detection found nothing, asking AI to classify page...")
        page_state = _ai_identify_page_state(driver)
        logging.info(f"AI page classification: '{page_state}'")

        if page_state == "success":
            logging.info("AI says application already succeeded")
            return True
        elif page_state == "chatbot":
            logging.info("AI detected chatbot that selectors missed, retrying with AI hints...")
            ai_input, _ = _ai_find_element(
                driver, None, "text_input",
                "the text input for answering chatbot screening questions"
            )
            if ai_input:
                chatbot_info = {
                    "chat_list": None, "text_input": ai_input,
                    "has_radios": False, "container": driver.find_element(By.TAG_NAME, "body"),
                }
                return _handle_naukri_chatbot(driver, chatbot_info, job_title, company, resume_context)
        elif page_state == "form":
            logging.info("AI detected form that selectors missed, using body as container...")
            container = driver.find_element(By.TAG_NAME, "body")
        else:
            logging.info("No questionnaire detected (AI agrees)")
            return False

    logging.info(f"Form questionnaire detected for '{job_title}' at {company}")
    save_screenshot(driver, f"form_questionnaire_{company.replace(' ', '_')[:20]}", "info")

    questions = _extract_form_questions(driver, container)
    if not questions:
        logging.warning("Form questionnaire detected but no extractable questions found")
        save_screenshot(driver, f"form_no_questions_{company.replace(' ', '_')[:20]}", "warning")
        return True

    answered_count = 0
    for q in questions:
        question_text = q["text"]
        input_type = q["input_type"]
        options = q["options"]

        answer, config_key, config_type = match_config(question_text)
        source = "config"

        if answer is None:
            source = "ollama"
            config_key = None
            answer = ask_ollama(question_text, options, resume_context)

        if not answer:
            log_qa(job_title, company, question_text, input_type, "", "skipped", None)
            continue

        if _fill_form_answer(driver, q, answer):
            answered_count += 1

        log_qa(job_title, company, question_text, input_type, answer, source, config_key)
        time.sleep(random.uniform(0.5, 1.5))

    logging.info(f"Filled {answered_count}/{len(questions)} form questions")
    save_screenshot(driver, f"form_filled_{company.replace(' ', '_')[:20]}", "info")

    time.sleep(random.uniform(1, 2.5))
    submitted = _click_form_submit(driver, container)

    if submitted:
        time.sleep(random.uniform(3, 6))
        save_screenshot(driver, f"form_submitted_{company.replace(' ', '_')[:20]}", "info")
        new_container = _detect_form_questionnaire(driver)
        if new_container:
            logging.info("Multi-page form detected, handling next page...")
            return handle_questionnaire(driver, job_title, company)

    return True
