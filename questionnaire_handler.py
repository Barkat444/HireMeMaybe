import os
import json
import time
import random
import logging
import requests
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from utils import save_screenshot

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCREENING_ANSWERS_FILE = os.path.join(SCRIPT_DIR, "screening_answers.json")
QA_LOG_FILE = os.path.join(SCRIPT_DIR, "qa_log.json")

_screening_config = None
_resume_text_cache = None


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


def match_config(question_text):
    """Match question text against screening_answers.json keyword patterns.
    Returns (answer_value, answer_key, answer_type) or (None, None, None)."""
    config = _load_screening_config()
    question_lower = question_text.lower().strip()

    for entry in config.get("questions", []):
        for keyword in entry["keywords"]:
            if keyword.lower() in question_lower:
                answer_key = entry["answer_key"]
                answer_value = config["profile"].get(answer_key, "")
                if answer_value:
                    return answer_value, answer_key, entry.get("type", "text")
                break

    return None, None, None


def ask_ollama(question_text, options, resume_context):
    """Call the local Ollama instance to answer a question.
    Returns the answer string, or empty string on failure."""
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

    options_text = ""
    if options:
        options_text = "\nAvailable options:\n" + "\n".join(f"- {opt}" for opt in options)
        options_text += "\n\nYou MUST pick exactly one option from the list above. Return ONLY the option text, nothing else."

    resume_section = ""
    if resume_context:
        resume_section = f"\n\nCandidate Resume:\n{resume_context[:3000]}"

    system_prompt = (
        "You are filling out a job application screening form on behalf of a candidate. "
        "Answer concisely and accurately based on the candidate's resume. "
        "For numeric questions, return ONLY the number. "
        "For yes/no questions, return ONLY 'Yes' or 'No'. "
        "For multiple choice, return ONLY the exact option text that best matches. "
        "Do not add explanations, preamble, or extra text. Just the answer."
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
        answer = data.get("message", {}).get("content", "").strip()
        logging.info(f"Ollama answered: '{answer}' for question: '{question_text[:80]}'")
        return answer
    except requests.exceptions.ConnectionError:
        logging.error("Cannot connect to Ollama. Is it running? Check OLLAMA_URL in .env")
        return ""
    except Exception as e:
        logging.error(f"Ollama request failed: {e}")
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


def _detect_questionnaire(driver):
    """Detect if a questionnaire sidebar/dialog appeared after clicking Apply.
    Returns the container element or None."""
    sidebar_selectors = [
        "[class*='questionnaire']",
        "[class*='screening']",
        "[class*='chatbot_drawer']",
        "[class*='apply-dialog']",
        "[class*='ApplyForm']",
        "[class*='applyForm']",
        "[class*='apply_dialog']",
        "[class*='screeningQues']",
        "[class*='ScreeningQues']",
        ".chatbot_drawer",
        ".styles_chatbot-drawer",
        "[class*='chatbot-drawer']",
    ]

    for sel in sidebar_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in elements:
                if el.is_displayed() and el.size.get("height", 0) > 50:
                    logging.info(f"Detected questionnaire container with selector: {sel}")
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
                logging.info("Detected questionnaire inside dialog/modal")
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
                inputs = form.find_elements(By.CSS_SELECTOR,
                    "input, select, textarea")
                if len(inputs) >= 1:
                    logging.info("Detected questionnaire form via keyword heuristic")
                    return form
    except Exception:
        pass

    return None


def _extract_questions(driver, container):
    """Extract all questions and their input elements from the questionnaire container.
    Returns list of dicts: {text, input_type, element, options[], parent}."""
    questions = []

    question_block_selectors = [
        "[class*='question']",
        "[class*='Question']",
        "[class*='ques-']",
        "[class*='chatbot_message']",
        "[class*='screenQues']",
        ".msg",
        ".botMsg",
        "[class*='bot-msg']",
        "[class*='botMsg']",
        "[class*='formGroup']",
        "[class*='form-group']",
        ".form-group",
    ]

    question_blocks = []
    for sel in question_block_selectors:
        try:
            blocks = container.find_elements(By.CSS_SELECTOR, sel)
            if blocks:
                question_blocks = blocks
                logging.info(f"Found {len(blocks)} question blocks with selector: {sel}")
                break
        except Exception:
            continue

    if not question_blocks:
        try:
            labels = container.find_elements(By.CSS_SELECTOR, "label")
            for label in labels:
                if not label.is_displayed() or not label.text.strip():
                    continue
                parent = driver.execute_script("return arguments[0].parentElement;", label)
                if parent:
                    question_blocks.append(parent)
            if question_blocks:
                logging.info(f"Found {len(question_blocks)} question blocks via labels")
        except Exception:
            pass

    if not question_blocks:
        question_blocks = [container]
        logging.info("Using container itself as single question block")

    for block in question_blocks:
        try:
            if not block.is_displayed():
                continue

            question_text = ""
            try:
                label_els = block.find_elements(By.CSS_SELECTOR, "label, [class*='label'], [class*='question-text'], .msg-text, [class*='msgText']")
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

            try:
                radios = block.find_elements(By.CSS_SELECTOR, "input[type='radio']")
                if radios:
                    input_type = "radio"
                    input_element = radios
                    for radio in radios:
                        try:
                            label = driver.execute_script(
                                "var id = arguments[0].id; "
                                "if(id) return document.querySelector('label[for=\"'+id+'\"]'); "
                                "return arguments[0].parentElement;",
                                radio
                            )
                            opt_text = label.text.strip() if label else ""
                            if not opt_text:
                                opt_text = radio.get_attribute("value") or ""
                            if opt_text:
                                options.append(opt_text)
                        except Exception:
                            val = radio.get_attribute("value") or ""
                            if val:
                                options.append(val)
            except Exception:
                pass

            if input_type == "unknown":
                try:
                    selects = block.find_elements(By.CSS_SELECTOR, "select")
                    if selects:
                        input_type = "select"
                        input_element = selects[0]
                        opt_els = selects[0].find_elements(By.TAG_NAME, "option")
                        options = [
                            o.text.strip() for o in opt_els
                            if o.text.strip() and o.get_attribute("value") != ""
                        ]
                except Exception:
                    pass

            if input_type == "unknown":
                try:
                    checkboxes = block.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
                    if checkboxes:
                        input_type = "checkbox"
                        input_element = checkboxes
                        for cb in checkboxes:
                            try:
                                label = driver.execute_script(
                                    "var id = arguments[0].id; "
                                    "if(id) return document.querySelector('label[for=\"'+id+'\"]'); "
                                    "return arguments[0].parentElement;",
                                    cb
                                )
                                opt_text = label.text.strip() if label else cb.get_attribute("value") or ""
                                if opt_text:
                                    options.append(opt_text)
                            except Exception:
                                val = cb.get_attribute("value") or ""
                                if val:
                                    options.append(val)
                except Exception:
                    pass

            if input_type == "unknown":
                try:
                    textareas = block.find_elements(By.CSS_SELECTOR, "textarea")
                    if textareas:
                        input_type = "textarea"
                        input_element = textareas[0]
                except Exception:
                    pass

            if input_type == "unknown":
                try:
                    text_inputs = block.find_elements(By.CSS_SELECTOR,
                        "input[type='text'], input[type='number'], input:not([type])")
                    for ti in text_inputs:
                        if ti.is_displayed():
                            input_type = ti.get_attribute("type") or "text"
                            input_element = ti
                            break
                except Exception:
                    pass

            if input_type == "unknown":
                try:
                    clickable_opts = block.find_elements(By.CSS_SELECTOR,
                        "[class*='option'], [class*='chip'], [class*='choice'], button[class*='opt']")
                    if clickable_opts:
                        input_type = "clickable_options"
                        input_element = clickable_opts
                        options = [opt.text.strip() for opt in clickable_opts if opt.text.strip()]
                except Exception:
                    pass

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
            logging.warning("Stale element while extracting questions, skipping block")
            continue
        except Exception as e:
            logging.warning(f"Error extracting question block: {e}")
            continue

    logging.info(f"Extracted {len(questions)} answerable questions from questionnaire")
    return questions


def _fill_answer(driver, question, answer):
    """Fill in the answer for a single question based on its input type."""
    input_type = question["input_type"]
    element = question["element"]
    options = question["options"]

    try:
        if input_type in ("text", "number"):
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            element.clear()
            time.sleep(random.uniform(0.3, 0.8))
            element.send_keys(str(answer))
            logging.info(f"Filled text input: '{answer}'")
            return True

        elif input_type == "textarea":
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            element.clear()
            time.sleep(random.uniform(0.3, 0.8))
            element.send_keys(str(answer))
            logging.info(f"Filled textarea: '{answer[:60]}...'")
            return True

        elif input_type == "select":
            matched_option = _fuzzy_match_option(answer, options)
            if matched_option:
                opt_elements = element.find_elements(By.TAG_NAME, "option")
                for opt_el in opt_elements:
                    if opt_el.text.strip() == matched_option:
                        opt_el.click()
                        logging.info(f"Selected dropdown option: '{matched_option}'")
                        return True
                from selenium.webdriver.support.ui import Select
                select = Select(element)
                select.select_by_visible_text(matched_option)
                logging.info(f"Selected dropdown option via Select: '{matched_option}'")
                return True
            logging.warning(f"No matching dropdown option for answer: '{answer}'")
            return False

        elif input_type == "radio":
            matched_option = _fuzzy_match_option(answer, options)
            radios = element
            for i, radio in enumerate(radios):
                radio_label = options[i] if i < len(options) else ""
                if radio_label.lower().strip() == (matched_option or answer).lower().strip():
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", radio)
                    driver.execute_script("arguments[0].click();", radio)
                    logging.info(f"Selected radio option: '{radio_label}'")
                    return True
            if radios:
                driver.execute_script("arguments[0].click();", radios[0])
                logging.info(f"Selected first radio option as fallback: '{options[0] if options else 'unknown'}'")
                return True
            return False

        elif input_type == "checkbox":
            checkboxes = element
            answer_lower = answer.lower()
            for i, cb in enumerate(checkboxes):
                cb_label = options[i] if i < len(options) else ""
                if cb_label.lower() in answer_lower or answer_lower in cb_label.lower():
                    if not cb.is_selected():
                        driver.execute_script("arguments[0].click();", cb)
                        logging.info(f"Checked checkbox: '{cb_label}'")
            return True

        elif input_type == "clickable_options":
            clickable_opts = element
            matched_option = _fuzzy_match_option(answer, options)
            for opt_el in clickable_opts:
                if opt_el.text.strip().lower() == (matched_option or answer).lower().strip():
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", opt_el)
                    driver.execute_script("arguments[0].click();", opt_el)
                    logging.info(f"Clicked option: '{opt_el.text.strip()}'")
                    return True
            if clickable_opts:
                driver.execute_script("arguments[0].click();", clickable_opts[0])
                logging.info(f"Clicked first option as fallback")
                return True
            return False

    except Exception as e:
        logging.error(f"Error filling answer for '{question['text'][:50]}': {e}")
        return False

    return False


def _click_submit(driver, container):
    """Find and click the submit/save/next button in the questionnaire."""
    submit_selectors = [
        "button[type='submit']",
        "[class*='submit']",
        "[class*='Submit']",
        "button[class*='save']",
        "button[class*='Save']",
        "button[class*='apply']",
        "button[class*='Apply']",
        "button[class*='next']",
        "button[class*='Next']",
        "button[class*='send']",
        "button[class*='Send']",
        "input[type='submit']",
    ]

    for sel in submit_selectors:
        try:
            buttons = container.find_elements(By.CSS_SELECTOR, sel)
            for btn in buttons:
                if btn.is_displayed():
                    btn_text = btn.text.strip().lower()
                    if any(word in btn_text for word in ["submit", "save", "apply", "next", "send", "continue", "done"]):
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                        time.sleep(random.uniform(0.5, 1.5))
                        driver.execute_script("arguments[0].click();", btn)
                        logging.info(f"Clicked questionnaire submit button: '{btn.text.strip()}'")
                        return True
        except Exception:
            continue

    try:
        all_buttons = container.find_elements(By.CSS_SELECTOR, "button, input[type='submit'], a[class*='btn']")
        for btn in all_buttons:
            if not btn.is_displayed():
                continue
            btn_text = btn.text.strip().lower()
            if any(word in btn_text for word in ["submit", "save", "apply", "next", "send", "continue", "done"]):
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                time.sleep(random.uniform(0.5, 1.5))
                driver.execute_script("arguments[0].click();", btn)
                logging.info(f"Clicked submit button (broad search): '{btn.text.strip()}'")
                return True
    except Exception:
        pass

    try:
        page_buttons = driver.find_elements(By.XPATH,
            "//button[contains(text(),'Submit') or contains(text(),'Save') or "
            "contains(text(),'Apply') or contains(text(),'Next') or "
            "contains(text(),'Send') or contains(text(),'Continue') or "
            "contains(text(),'Done')]")
        for btn in page_buttons:
            if btn.is_displayed():
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                time.sleep(random.uniform(0.5, 1.5))
                driver.execute_script("arguments[0].click();", btn)
                logging.info(f"Clicked submit button (page-wide XPath): '{btn.text.strip()}'")
                return True
    except Exception:
        pass

    logging.warning("Could not find submit button in questionnaire")
    return False


def handle_questionnaire(driver, job_title, company):
    """Main entry point: detect, extract, answer, and submit the questionnaire.
    Returns True if a questionnaire was detected and handled, False otherwise."""
    if os.getenv("ANSWER_QUESTIONNAIRE", "false").lower() != "true":
        return False

    container = _detect_questionnaire(driver)
    if not container:
        return False

    logging.info(f"Questionnaire detected for '{job_title}' at {company}")
    save_screenshot(driver, f"questionnaire_detected_{company.replace(' ', '_')[:20]}", "info")

    resume_context = load_resume_text()

    questions = _extract_questions(driver, container)
    if not questions:
        logging.warning("Questionnaire detected but no extractable questions found")
        save_screenshot(driver, f"questionnaire_no_questions_{company.replace(' ', '_')[:20]}", "warning")
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
            logging.info(f"No config match for: '{question_text[:80]}', asking Ollama...")
            answer = ask_ollama(question_text, options, resume_context)

        if not answer:
            logging.warning(f"No answer available for: '{question_text[:80]}', skipping this field")
            log_qa(job_title, company, question_text, input_type, "", "skipped", None)
            continue

        filled = _fill_answer(driver, q, answer)
        if filled:
            answered_count += 1

        log_qa(job_title, company, question_text, input_type, answer, source, config_key)
        time.sleep(random.uniform(0.5, 1.5))

    logging.info(f"Answered {answered_count}/{len(questions)} questions")
    save_screenshot(driver, f"questionnaire_filled_{company.replace(' ', '_')[:20]}", "info")

    time.sleep(random.uniform(1, 2.5))

    submitted = _click_submit(driver, container)

    if submitted:
        time.sleep(random.uniform(3, 6))
        save_screenshot(driver, f"questionnaire_submitted_{company.replace(' ', '_')[:20]}", "info")

        new_container = _detect_questionnaire(driver)
        if new_container:
            logging.info("Multi-page questionnaire detected, handling next page...")
            return handle_questionnaire(driver, job_title, company)

    return True
