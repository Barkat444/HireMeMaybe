import random
import re
import time
import os
import logging
import requests
from datetime import datetime, timedelta
from urllib.parse import quote_plus
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from utils import init_driver, login, save_screenshot
from rotate_headline import setup_logging, clear_debug_images
from questionnaire_handler import handle_questionnaire

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEBUG_DIR = os.path.join(SCRIPT_DIR, "debug")
APPLIED_JOBS_FILE = os.path.join(DEBUG_DIR, "applied_jobs.txt")

os.makedirs(DEBUG_DIR, exist_ok=True)

GENERIC_TITLE_WORDS = {
    "engineer", "developer", "manager", "lead", "senior", "junior",
    "associate", "principal", "staff", "intern", "analyst", "specialist",
    "consultant", "architect", "administrator", "admin", "coordinator",
    "director", "head", "vp", "mid", "level", "i", "ii", "iii",
}

COMMON_ABBREVIATIONS = {
    "site reliability engineer": ["sre"],
    "site reliability": ["sre"],
    "devops engineer": ["devops", "dev ops", "dev-ops"],
    "devops": ["dev ops", "dev-ops"],
    "machine learning": ["ml"],
    "artificial intelligence": ["ai"],
    "data engineer": ["data engineering"],
    "data scientist": ["data science"],
    "quality assurance": ["qa"],
    "user experience": ["ux"],
    "user interface": ["ui"],
    "full stack": ["fullstack", "full-stack"],
    "front end": ["frontend", "front-end"],
    "back end": ["backend", "back-end"],
    "business analyst": ["ba"],
    "project manager": ["pm"],
    "product manager": ["pm"],
    "technical lead": ["tech lead"],
    "software development engineer in test": ["sdet"],
    "database administrator": ["dba"],
}


def build_relevance_keywords(job_titles):
    """Build keyword set dynamically from whatever job titles the user configures.

    Strategy:
    1. Full title phrases as-is (highest priority during matching)
    2. Multi-word domain phrases (non-generic words joined, e.g., 'site reliability')
    3. Hyphenated/compact variants (e.g., 'devops' from 'dev ops')
    4. Known abbreviations if the title matches (e.g., 'sre' from 'site reliability engineer')
    5. Individual significant words (>=4 chars, not in GENERIC_TITLE_WORDS)
    """
    keywords = set()

    for title in job_titles:
        title_lower = title.lower().strip()
        keywords.add(title_lower)

        words = title_lower.split()
        significant = [w for w in words if w not in GENERIC_TITLE_WORDS and len(w) > 2]

        if len(significant) >= 2:
            phrase = " ".join(significant)
            keywords.add(phrase)
            keywords.add(phrase.replace(" ", "-"))
            keywords.add(phrase.replace(" ", ""))

        for known_phrase, abbrevs in COMMON_ABBREVIATIONS.items():
            if known_phrase in title_lower:
                keywords.update(abbrevs)

        for word in significant:
            if len(word) >= 4:
                keywords.add(word)

    return keywords


def is_job_relevant(text, relevance_keywords, strict=False):
    """Check if text contains relevance keywords.

    strict=False (titles): any single keyword match is enough.
    strict=True  (JD text): requires either a multi-word phrase match (>=2 words)
                            OR at least 2 distinct single-word keyword hits.
    """
    text_lower = text.lower()
    sorted_kws = sorted(relevance_keywords, key=len, reverse=True)

    if not strict:
        for keyword in sorted_kws:
            if keyword in text_lower:
                return True, keyword
        return False, None

    for keyword in sorted_kws:
        if " " in keyword and keyword in text_lower:
            return True, keyword

    single_hits = []
    for keyword in sorted_kws:
        if " " not in keyword and keyword in text_lower:
            single_hits.append(keyword)
            if len(single_hits) >= 2:
                return True, f"{single_hits[0]}+{single_hits[1]}"

    return False, None


_ollama_available = None


def _is_ollama_available():
    """Check if Ollama is reachable. Result is cached for the entire session
    so we don't repeatedly timeout when Ollama isn't running."""
    global _ollama_available
    if _ollama_available is not None:
        return _ollama_available

    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    try:
        resp = requests.get(f"{ollama_url}/api/tags", timeout=3)
        _ollama_available = resp.status_code == 200
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        _ollama_available = False

    if _ollama_available:
        logging.info("Ollama is available -- AI relevance checks enabled")
    else:
        logging.info("Ollama is not reachable -- AI relevance checks disabled, using static keywords only")
    return _ollama_available


def ai_check_relevance(job_title, job_titles_config, text_context="", timeout=10):
    """Ask Ollama whether a job listing matches the candidate's target roles.

    Runs only when Ollama is available. Returns (is_relevant, reason).
    On any failure, returns (False, "") so the caller can fall through
    to the next check gracefully.
    """
    if not _is_ollama_available():
        return False, ""

    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

    system_prompt = (
        "You are a strict job relevance classifier. "
        "The user is searching for specific roles. "
        "You must decide if the job listing is a genuine match for the target roles.\n"
        "Rules:\n"
        "1. Return ONLY 'YES' or 'NO' followed by a dash and a 3-8 word reason.\n"
        "2. Be STRICT: the job must be the same function/domain as the target roles.\n"
        "3. Generic IT support, testing, unrelated consulting, or tangentially related roles are NOT relevant.\n"
        "4. Consider title, responsibilities, and required skills if JD is provided.\n"
        "5. Example output: 'YES - devops infrastructure role' or 'NO - frontend web developer role'."
    )

    jd_section = ""
    if text_context:
        jd_section = f"\n\nJob Description excerpt:\n{text_context[:2000]}"

    user_prompt = (
        f"Target roles the candidate is looking for: {job_titles_config}\n"
        f"Job listing title: {job_title}"
        f"{jd_section}\n\n"
        "Is this job relevant to the target roles? Answer YES or NO with reason."
    )

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
                "options": {"temperature": 0.0},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        raw = data.get("message", {}).get("content", "").strip()

        first_word = raw.split()[0].upper().rstrip(".,:-") if raw else ""
        is_relevant = first_word == "YES"
        reason = raw[:100]

        return is_relevant, reason

    except requests.exceptions.ConnectionError:
        global _ollama_available
        _ollama_available = False
        logging.warning("Ollama connection lost during relevance check, disabling AI checks")
        return False, ""
    except Exception as e:
        logging.debug(f"AI relevance check failed: {e}")
        return False, ""


def load_applied_jobs():
    """Load previously applied job URLs from the dedup file.
    Handles migration from old root-level location to debug/."""
    old_path = os.path.join(SCRIPT_DIR, "applied_jobs.txt")
    if os.path.exists(old_path) and not os.path.exists(APPLIED_JOBS_FILE):
        os.rename(old_path, APPLIED_JOBS_FILE)
        logging.info("Migrated applied_jobs.txt to debug/ folder")

    if not os.path.exists(APPLIED_JOBS_FILE):
        return set()
    with open(APPLIED_JOBS_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def save_applied_job(job_url):
    """Append a newly applied job URL to the dedup file."""
    with open(APPLIED_JOBS_FILE, "a", encoding="utf-8") as f:
        f.write(job_url + "\n")


def build_search_url(job_title, location, experience, freshness_days=1, page=1):
    """Build a Naukri search URL with all filters baked in.

    URL pattern (confirmed working):
      https://www.naukri.com/{slug}-jobs-in-{location}?k={keyword}&l={location}&experience={exp}&jobAge={days}

    Naukri supports URL-based pagination by appending -2, -3, etc. to the path:
      https://www.naukri.com/{slug}-jobs-in-{location}-2?k=...

    Naukri defaults to 'Recommended' sort which blends relevance with recency.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", job_title.lower()).strip("-")
    loc_slug = re.sub(r"[^a-z0-9]+", "-", location.lower()).strip("-")
    keyword_encoded = quote_plus(job_title)
    loc_encoded = quote_plus(location)

    page_suffix = f"-{page}" if page > 1 else ""

    return (
        f"https://www.naukri.com/{slug}-jobs-in-{loc_slug}{page_suffix}"
        f"?k={keyword_encoded}&l={loc_encoded}"
        f"&experience={experience}&jobAge={freshness_days}"
    )


def apply_for_jobs():
    """Search Naukri.com for jobs and apply.

    Flow:
    1. Log in
    2. For each job title, build a URL with keyword + location + experience + freshness
    3. Navigate directly (no UI filter clicking) -- Naukri defaults to 'Recommended' sort
    4. Process listings with strict relevance checks
    5. Paginate if needed, try next title if quota not met
    """
    setup_logging()
    clear_debug_images()

    job_titles = os.getenv("JOB_TITLES", "DevOps Engineer, Site Reliability Engineer").split(",")
    job_titles = [title.strip() for title in job_titles if title.strip()]

    locations = os.getenv("JOB_LOCATIONS", "Remote").split(",")
    locations = [loc.strip() for loc in locations if loc.strip()]

    experience = os.getenv("JOB_EXPERIENCE", "2")
    max_applications = int(os.getenv("MAX_APPLICATIONS", "3"))
    freshness_days = int(os.getenv("JOB_FRESHNESS_DAYS", "1"))

    relevance_keywords = build_relevance_keywords(job_titles)
    logging.info(f"Relevance keywords: {relevance_keywords}")

    applied_jobs = load_applied_jobs()
    logging.info(f"Loaded {len(applied_jobs)} previously applied job URLs for deduplication")

    applied_count = 0
    driver = init_driver()

    try:
        logging.info("Attempting to log in to Naukri.com")
        if not login(driver):
            logging.error("Login failed. Exiting job application process.")
            return 0
        logging.info("Logged in successfully")

        driver.get("https://www.naukri.com/mnjuser/profile")
        logging.info("Navigated to profile page")
        time.sleep(random.uniform(4, 7))

        logging.info(f"Target: Apply to {max_applications} jobs across {len(job_titles)} title(s)")

        freshness_tiers = [7, 3, 1]
        if freshness_days not in freshness_tiers:
            freshness_tiers = [freshness_days] + [f for f in freshness_tiers if f != freshness_days]

        for fresh_days in freshness_tiers:
            if applied_count >= max_applications:
                break

            titles_shuffled = job_titles[:]
            random.shuffle(titles_shuffled)

            logging.info(f"--- Freshness tier: last {fresh_days} day(s) ---")

            for title in titles_shuffled:
                if applied_count >= max_applications:
                    break

                location = random.choice(locations)
                search_url = build_search_url(title, location, experience, fresh_days)
                logging.info(f"Searching: '{title}' in {location} (exp={experience}, fresh={fresh_days}d)")
                logging.info(f"URL: {search_url}")

                search_for_jobs(driver, search_url)

                search_ctx = {
                    "title": title, "location": location,
                    "experience": experience, "freshness_days": fresh_days,
                }
                remaining = max_applications - applied_count
                new_apps = process_job_listings(
                    driver, remaining, relevance_keywords, applied_jobs,
                    search_context=search_ctx,
                )
                applied_count += new_apps

                if applied_count < max_applications:
                    time.sleep(random.uniform(3, 6))

            if applied_count > 0:
                logging.info(f"Found {applied_count} jobs at {fresh_days}d freshness, stopping tier search")
                break
            else:
                logging.info(f"No jobs applied at {fresh_days}d freshness, trying narrower window...")

        if applied_count >= max_applications:
            logging.info(f"Successfully applied to {applied_count} jobs (reached target of {max_applications})")
        else:
            logging.info(f"Applied to {applied_count} jobs (target was {max_applications})")

    except Exception as e:
        logging.error(f"Error during job application process: {e}")
        save_screenshot(driver, "job_application_error", "failure")
    finally:
        driver.quit()
        logging.info("Browser closed")

        interval_hours = int(os.getenv("INTERVAL_HOURS", "1"))
        if interval_hours > 0:
            next_run = datetime.now() + timedelta(hours=interval_hours)
            logging.info(f"Next scheduled run: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
            logging.info(f"Will run every {interval_hours} hour(s)")

    return applied_count

def search_for_jobs(driver, search_url):
    """Navigate directly to a pre-built Naukri search URL.

    All filters (keyword, location, experience, freshness) are in the URL.
    Naukri defaults to 'Recommended' sort which is the best blend of
    relevance + recency. No UI clicking needed.
    """
    logging.info("Navigating to search results...")

    try:
        driver.get(search_url)
        time.sleep(random.uniform(4, 7))

        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    ".srp-jobtuple-wrapper, .jobTupleHeader, .cust-job-tuple, "
                    ".jobTuple, div[type='tuple']"))
            )
            logging.info("Search results loaded successfully")
        except TimeoutException:
            logging.warning("Timed out waiting for search results, proceeding anyway")

        save_screenshot(driver, "job_search_results", "success")

    except Exception as e:
        logging.error(f"Error loading search results: {e}")
        save_screenshot(driver, "job_search_error", "failure")


def _click_next_page_button(driver, current_page):
    """Try to find and click the next-page button using expanded selectors + AI fallback.
    Returns True if the next page loaded successfully."""

    next_page_num = current_page + 1

    pagination_selectors = [
        f"a[href*='-{next_page_num}?']",
        f"a[title='Page {next_page_num}']",
        f"a[data-page='{next_page_num}']",
        ".fright.fs14.btn-secondary.br2",
        "a.fright",
        "a[title='Next']",
        ".pagination a.next",
        "[class*='pagination'] a.fright",
        "[class*='paginat'] a:last-child",
        "a[class*='nxt']",
        "a[class*='next']",
        "span[class*='next'] a",
    ]

    for sel in pagination_selectors:
        try:
            buttons = driver.find_elements(By.CSS_SELECTOR, sel)
            for btn in buttons:
                if btn.is_displayed():
                    logging.info(f"Found next-page button via selector: {sel}")
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                    time.sleep(random.uniform(0.5, 1.0))
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(random.uniform(4, 7))
                    return True
        except Exception:
            continue

    try:
        xpath_patterns = [
            f"//a[contains(@href, '-{next_page_num}?')]",
            "//a[contains(@class, 'fright') and (contains(@class, 'btn') or contains(@class, 'next'))]",
            "//*[contains(@class, 'paginat')]//a[last()]",
            "//a[text()='Next' or text()='next' or text()='>']",
            f"//a[text()='{next_page_num}']",
        ]
        for xp in xpath_patterns:
            els = driver.find_elements(By.XPATH, xp)
            for el in els:
                if el.is_displayed():
                    logging.info(f"Found next-page button via XPath: {xp}")
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
                    time.sleep(random.uniform(0.5, 1.0))
                    driver.execute_script("arguments[0].click();", el)
                    time.sleep(random.uniform(4, 7))
                    return True
    except Exception:
        pass

    if _is_ollama_available():
        try:
            page_text = driver.execute_script(
                "return document.querySelector('body').innerText.substring("
                "document.querySelector('body').innerText.length - 3000);"
            )
            pagination_html = ""
            try:
                pagination_html = driver.execute_script("""
                    var els = document.querySelectorAll(
                        '[class*="paginat"], [class*="Paginat"], nav, .pagesList'
                    );
                    var html = '';
                    els.forEach(function(el) { html += el.outerHTML + '\\n'; });
                    return html.substring(0, 2000);
                """)
            except Exception:
                pass

            prompt = (
                f"I am on page {current_page} of Naukri.com job search results. "
                f"I need to navigate to page {next_page_num}.\n\n"
                "Find the CSS selector or XPath for the next-page link/button. "
                "Return ONLY one selector like:\ncss: <selector>\nor\nxpath: <selector>\n\n"
            )
            if pagination_html:
                prompt += f"Pagination HTML:\n{pagination_html}\n\n"
            else:
                prompt += f"Page bottom text:\n{page_text[-1500:]}\n\n"

            ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
            ollama_model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
            response = requests.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": ollama_model,
                    "messages": [
                        {"role": "system", "content": "You are a web automation assistant. Return ONLY the selector, no explanation."},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.0},
                },
                timeout=15,
            )
            response.raise_for_status()
            raw = response.json().get("message", {}).get("content", "").strip()
            logging.info(f"AI pagination suggestion: {raw[:100]}")

            for line in raw.split("\n"):
                line = line.strip()
                if line.startswith("css:"):
                    sel = line[4:].strip().strip("'\"")
                    els = driver.find_elements(By.CSS_SELECTOR, sel)
                    for el in els:
                        if el.is_displayed():
                            driver.execute_script("arguments[0].click();", el)
                            logging.info(f"AI clicked next-page via CSS: {sel}")
                            time.sleep(random.uniform(4, 7))
                            return True
                elif line.startswith("xpath:"):
                    sel = line[6:].strip().strip("'\"")
                    els = driver.find_elements(By.XPATH, sel)
                    for el in els:
                        if el.is_displayed():
                            driver.execute_script("arguments[0].click();", el)
                            logging.info(f"AI clicked next-page via XPath: {sel}")
                            time.sleep(random.uniform(4, 7))
                            return True
        except Exception as e:
            logging.debug(f"AI pagination fallback failed: {e}")

    logging.info("All pagination methods exhausted")
    return False


def process_job_listings(driver, max_applications, relevance_keywords, applied_jobs,
                         page=1, max_pages=3, search_context=None):
    """
    Process the job listings page and apply to suitable jobs.
    Paginates up to max_pages when more relevant jobs are needed.
    """
    applied_count = 0
    tabs_opened = 0
    max_tabs_to_open = max_applications * 5
    
    try:
        job_selectors = [
            ".srp-jobtuple-wrapper",
            ".jobTuple",
            ".cust-job-tuple",
            "div[type='tuple']",
            ".jobTupleHeader",
            "article.jobTupleHeader",
        ]
        
        job_listings = []
        for selector in job_selectors:
            listings = driver.find_elements(By.CSS_SELECTOR, selector)
            if listings:
                job_listings = listings
                logging.info(f"Found {len(listings)} job listings with selector '{selector}' (page {page})")
                break
        
        if not job_listings:
            logging.error(f"No job listings found on page {page}")
            save_screenshot(driver, f"no_job_listings_page_{page}", "failure")
            return 0
        
        logging.info(f"Found {len(job_listings)} job listings on page {page}, targeting {max_applications} applications")
    
        for index, job in enumerate(job_listings):
            if applied_count >= max_applications:
                logging.info(f"✓ Reached target application limit ({max_applications})")
                break
                
            if tabs_opened >= max_tabs_to_open:
                logging.info(f"Reached maximum tab-open limit ({max_tabs_to_open})")
                break
                
            try:
                job_title_elements = job.find_elements(By.CSS_SELECTOR, "a.title")
                if not job_title_elements:
                    job_title_elements = job.find_elements(By.CSS_SELECTOR, "a[title]")
                
                if not job_title_elements:
                    logging.warning(f"Could not find job title element for job {index+1}, skipping")
                    continue
                
                job_title_element = job_title_elements[0]
                job_title = job_title_element.text.strip()
                
                try:
                    company_elements = job.find_elements(By.CSS_SELECTOR, ".comp-name, .company-name")
                    company = company_elements[0].text.strip() if company_elements else "Unknown Company"
                except:
                    company = "Unknown Company"
                    
                try:
                    location_elements = job.find_elements(By.CSS_SELECTOR, ".locWdth, .location")
                    location = location_elements[0].text.strip() if location_elements else "Unknown Location"
                except:
                    location = "Unknown Location"
                
                logging.info(f"Processing job {index+1}: {job_title} at {company} in {location}")
                
                main_window = driver.current_window_handle
                
                job_link = job_title_element.get_attribute("href")
                
                if not job_link:
                    logging.warning(f"No link found for job {index+1}, skipping")
                    continue
                
                if job_link in applied_jobs:
                    logging.info(f"⊘ Already applied to this job previously, skipping: {job_title}")
                    continue
                
                title_relevant, matched_keyword = is_job_relevant(job_title, relevance_keywords)
                if title_relevant:
                    logging.info(f"Title is relevant (matched: '{matched_keyword}')")
                else:
                    job_titles_config = os.getenv("JOB_TITLES", "DevOps Engineer, Site Reliability Engineer")
                    ai_relevant, ai_reason = ai_check_relevance(job_title, job_titles_config, timeout=10)
                    if ai_relevant:
                        title_relevant = True
                        matched_keyword = f"ai:{ai_reason[:60]}"
                        logging.info(f"AI title check: '{job_title}' -> RELEVANT ({ai_reason})")
                    elif ai_reason:
                        logging.info(f"AI title check: '{job_title}' -> NOT RELEVANT ({ai_reason}), skipping")
                        continue
                    else:
                        logging.info(f"Title '{job_title}' did not match keywords, will check JD for relevance")
                
                tabs_opened += 1
                
                driver.execute_script(f"window.open('{job_link}', '_blank');")
                
                time.sleep(random.uniform(2.5, 5))
                windows = driver.window_handles
                driver.switch_to.window(windows[-1])
                
                logging.info(f"Opened job details in new tab: {job_title}")
                
                apply_result = check_and_apply(driver, job_title, company, relevance_keywords, title_relevant)
                
                if apply_result:
                    applied_count += 1
                    applied_jobs.add(job_link)
                    save_applied_job(job_link)
                    logging.info(f"Progress: Applied to {applied_count}/{max_applications} jobs")
                    
                driver.close()
                driver.switch_to.window(main_window)
                logging.info("Returned to job listings page")
                
                time.sleep(random.uniform(2, 4))
                
            except Exception as e:
                logging.error(f"Error processing job listing: {e}")
                save_screenshot(driver, f"job_listing_error_{index}", "failure")
                
                try:
                    driver.switch_to.window(main_window)
                except:
                    pass
                
                continue
        
        if applied_count < max_applications and page < max_pages:
            next_page_loaded = False

            if search_context:
                try:
                    next_url = build_search_url(
                        search_context["title"], search_context["location"],
                        search_context["experience"], search_context["freshness_days"],
                        page=page + 1,
                    )
                    logging.info(f"Navigating to page {page + 1} via URL: {next_url}")
                    driver.get(next_url)
                    time.sleep(random.uniform(4, 7))
                    test_listings = driver.find_elements(By.CSS_SELECTOR,
                        ".srp-jobtuple-wrapper, .jobTupleHeader, .cust-job-tuple, "
                        ".jobTuple, div[type='tuple']")
                    if test_listings:
                        next_page_loaded = True
                        logging.info(f"Page {page + 1} loaded via URL ({len(test_listings)} listings)")
                    else:
                        logging.info(f"Page {page + 1} URL returned no listings, end of results")
                except Exception as e:
                    logging.warning(f"URL-based pagination failed: {e}")

            if not next_page_loaded:
                next_page_loaded = _click_next_page_button(driver, page)

            if next_page_loaded:
                additional_applications = process_job_listings(
                    driver, max_applications - applied_count, relevance_keywords, applied_jobs,
                    page=page + 1, max_pages=max_pages, search_context=search_context,
                )
                applied_count += additional_applications
            else:
                logging.info("No more pages available")
    
    except Exception as e:
        logging.error(f"Error finding job listings: {e}")
        save_screenshot(driver, "job_listings_error", "failure")
    
    return applied_count

def check_and_apply(driver, job_title, company, relevance_keywords, title_relevant):
    """
    Check if the job is relevant and has a direct apply button, then apply.
    If the listing title didn't match relevance keywords, the JD is checked
    as a second chance before skipping.
    """
    try:
        time.sleep(random.uniform(2.5, 5))
        
        screenshot_path = save_screenshot(driver, f"job_details_{job_title.replace(' ', '_')[:20]}", "info")
        logging.info(f"Screenshot saved: {screenshot_path}")
        
        if not title_relevant:
            jd_text = ""
            jd_selectors = [
                ".job-desc", ".jd-container", ".jobDesc",
                "section.job-desc", "div.job-desc",
                ".styles_job-desc-container__txpYf",
                "[class*='job-desc']", "[class*='jobDesc']",
            ]
            for sel in jd_selectors:
                jd_elements = driver.find_elements(By.CSS_SELECTOR, sel)
                if jd_elements:
                    jd_text = jd_elements[0].text.strip()
                    break

            if not jd_text:
                logging.info(f"⊘ Skipping irrelevant job: '{job_title}' at {company} - no JD container found")
                save_screenshot(driver, f"skipped_irrelevant_{company.replace(' ', '_')[:20]}", "info")
                return False

            jd_relevant, matched_keyword = is_job_relevant(jd_text, relevance_keywords, strict=True)
            if not jd_relevant:
                job_titles_config = os.getenv("JOB_TITLES", "DevOps Engineer, Site Reliability Engineer")
                ai_relevant, ai_reason = ai_check_relevance(
                    job_title, job_titles_config, text_context=jd_text[:2000], timeout=15
                )
                if ai_relevant:
                    jd_relevant = True
                    matched_keyword = f"ai:{ai_reason[:60]}"
                    logging.info(f"AI JD check: '{job_title}' at {company} -> RELEVANT ({ai_reason})")
                else:
                    if ai_reason:
                        logging.info(f"AI JD check: '{job_title}' at {company} -> NOT RELEVANT ({ai_reason})")
                    else:
                        logging.info(f"⊘ Skipping: '{job_title}' at {company} - no keyword match in title or JD")
                    save_screenshot(driver, f"skipped_irrelevant_{company.replace(' ', '_')[:20]}", "info")
                    return False
            logging.info(f"JD is relevant (matched: '{matched_keyword}')")
        
        company_site_buttons = driver.find_elements(By.XPATH, 
            "//*[contains(text(), 'Apply on company site') or contains(text(), 'Apply on Company Site')]")
        
        if company_site_buttons:
            logging.info(f"Job at {company} requires applying on company site - skipping")
            screenshot_path = save_screenshot(driver, f"skipped_company_site_{company.replace(' ', '_')[:20]}", "info")
            logging.info(f"Company site application screenshot saved: {screenshot_path}")
            return False
            
        apply_buttons = []
        
        selectors = [
            "//button[contains(text(), 'Apply') or contains(@class, 'apply')]",
            "//a[contains(text(), 'Apply') or contains(@class, 'apply')]",
            "//span[contains(text(), 'Apply') and not(contains(text(), 'company'))]",
            "//div[contains(text(), 'Apply') and not(contains(text(), 'company'))]",
            "//input[@value='Apply']",
            "//*[contains(@class, 'apply-button')]"
        ]
        
        for selector in selectors:
            buttons = driver.find_elements(By.XPATH, selector)
            apply_buttons.extend(buttons)
            
        if not apply_buttons:
            logging.info(f"No direct apply button found for job at {company} - skipping")
            screenshot_path = save_screenshot(driver, f"no_apply_button_{company.replace(' ', '_')[:20]}", "info")
            logging.info(f"No apply button screenshot saved: {screenshot_path}")
            return False
            
        apply_button = apply_buttons[0]
        logging.info(f"Found Apply button for job at {company}")
        
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", apply_button)
        time.sleep(random.uniform(1, 2.5))
        
        screenshot_path = save_screenshot(driver, f"before_apply_{company.replace(' ', '_')[:20]}", "info")
        logging.info(f"Before apply screenshot saved: {screenshot_path}")
        
        apply_button.click()
        logging.info(f"Clicked Apply button for job at {company}")
        time.sleep(random.uniform(4, 7))
        
        screenshot_path = save_screenshot(driver, f"after_apply_click_{company.replace(' ', '_')[:20]}", "info")
        logging.info(f"After apply click screenshot saved: {screenshot_path}")
        
        questionnaire_handled = handle_questionnaire(driver, job_title, company)
        if questionnaire_handled:
            logging.info("Questionnaire handled, checking for success...")
            time.sleep(random.uniform(3, 6))
        
        success = False
        matched_pattern = None
        
        dynamic_success_text = f"Applied to {job_title}"
        
        success_patterns = [
            dynamic_success_text,
            "You have successfully applied",
            "Application successful",
            "Applied successfully",
            "You have already applied",
            "Application confirmed",
            "successfully applied to",
            "Successfully applied"
        ]
        
        page_text = driver.page_source.lower()
        
        for pattern in success_patterns:
            if pattern.lower() in page_text:
                success = True
                matched_pattern = pattern
                logging.info(f"✓ Found success message in source: '{pattern}' for job at {company}")
                screenshot_path = save_screenshot(driver, f"application_success_{company.replace(' ', '_')[:20]}", "success")
                logging.info(f"Application success screenshot saved: {screenshot_path}")
                break
                
        if not success:
            success_xpath = (
                f"//*[contains(., 'Applied to') and contains(., '{job_title}')] | "
                "//*[contains(text(), 'successfully applied') or contains(text(), 'Successfully applied')]"
            )
            success_messages = driver.find_elements(By.XPATH, success_xpath)
            
            if success_messages:
                success = True
                message_text = success_messages[0].text.strip()
                matched_pattern = message_text
                logging.info(f"✓ Found visible success message: for '{job_title}' at {company}")
                
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", success_messages[0])
                time.sleep(random.uniform(1, 2.5))
                
                screenshot_path = save_screenshot(driver, f"application_success_visible_{company.replace(' ', '_')[:20]}", "success")
                logging.info(f"Visible success screenshot saved: {screenshot_path}")
                
        if not success:
            try:
                dialogs = driver.find_elements(By.CSS_SELECTOR, 
                    ".modal, .popup, .dialog, .overlay, [role='dialog']")
                
                if dialogs:
                    logging.info(f"Found dialog/popup when applying to job at {company}")
                    screenshot_path = save_screenshot(driver, f"application_dialog_{company.replace(' ', '_')[:20]}", "info")
                    logging.info(f"Dialog screenshot saved: {screenshot_path}")
                    
                    for dialog in dialogs:
                        confirm_buttons = dialog.find_elements(By.XPATH, 
                            ".//button[contains(text(), 'Submit') or contains(text(), 'Confirm') or contains(text(), 'Apply') or contains(text(), 'OK')]")
                        
                        if confirm_buttons:
                            driver.execute_script("arguments[0].style.border='3px solid red'", confirm_buttons[0])
                            time.sleep(random.uniform(1, 2.5))
                            
                            screenshot_path = save_screenshot(driver, f"before_dialog_confirmation_{company.replace(' ', '_')[:20]}", "info")
                            logging.info(f"Before dialog confirmation screenshot saved: {screenshot_path}")
                            
                            confirm_buttons[0].click()
                            logging.info(f"Clicked confirmation button in dialog for job at {company}")
                            time.sleep(random.uniform(4, 7))
                            
                            screenshot_path = save_screenshot(driver, f"after_dialog_confirmation_{company.replace(' ', '_')[:20]}", "info")
                            logging.info(f"After dialog confirmation screenshot saved: {screenshot_path}")
                            
                            page_text = driver.page_source.lower()
                            for pattern in success_patterns:
                                if pattern.lower() in page_text:
                                    success = True
                                    matched_pattern = pattern
                                    logging.info(f"✓ Found success message after confirmation: '{pattern}' for job at {company}")
                                    screenshot_path = save_screenshot(driver, f"application_success_confirmed_{company.replace(' ', '_')[:20]}", "success")
                                    logging.info(f"Success after confirmation screenshot saved: {screenshot_path}")
                                    break
                                    
                            if not success:
                                success_messages = driver.find_elements(By.XPATH, 
                                    "//*[contains(text(), 'successfully applied') or contains(text(), 'Successfully applied')]")
                                
                                if success_messages:
                                    success = True
                                    message_text = success_messages[0].text.strip()
                                    matched_pattern = message_text
                                    logging.info(f"✓ Found visible success message after confirmation: '{message_text}' for job at {company}")
                                    
                                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", success_messages[0])
                                    time.sleep(random.uniform(1, 2.5))
                                    
                                    screenshot_path = save_screenshot(driver, f"application_success_visible_confirmed_{company.replace(' ', '_')[:20]}", "success")
                                    logging.info(f"Visible success after confirmation screenshot saved: {screenshot_path}")
                                    
            except Exception as e:
                logging.error(f"Error handling application confirmation: {e}")
                screenshot_path = save_screenshot(driver, f"confirmation_error_{company.replace(' ', '_')[:20]}", "failure")
                logging.info(f"Confirmation error screenshot saved: {screenshot_path}")
        
        if success:
            logging.info(f"✅ SUCCESSFULLY APPLIED TO JOB: {job_title} at {company}")
            if matched_pattern:
                logging.info(f"✅ Success")
            
            driver.execute_script("""
                var successDiv = document.createElement('div');
                successDiv.style.position = 'fixed';
                successDiv.style.top = '10px';
                successDiv.style.left = '10px';
                successDiv.style.backgroundColor = 'green';
                successDiv.style.color = 'white';
                successDiv.style.padding = '10px';
                successDiv.style.borderRadius = '5px';
                successDiv.style.zIndex = '9999';
                successDiv.style.fontWeight = 'bold';
                successDiv.textContent = 'SUCCESSFULLY APPLIED';
                document.body.appendChild(successDiv);
            """)
            time.sleep(random.uniform(1, 2.5))
            screenshot_path = save_screenshot(driver, f"final_success_{company.replace(' ', '_')[:20]}", "success")
            logging.info(f"Final success screenshot saved: {screenshot_path}")
            return True
        else:
            logging.warning(f"❌ Could not confirm successful application to job at {company}")
            screenshot_path = save_screenshot(driver, f"final_unconfirmed_{company.replace(' ', '_')[:20]}", "warning")
            logging.info(f"Final unconfirmed application screenshot saved: {screenshot_path}")
            return False
        
    except Exception as e:
        logging.error(f"Error applying for job: {e}")
        screenshot_path = save_screenshot(driver, f"application_error_{company.replace(' ', '_')[:20]}", "failure")
        logging.info(f"Application error screenshot saved: {screenshot_path}")
        return False

if __name__ == "__main__":
    apply_for_jobs()
