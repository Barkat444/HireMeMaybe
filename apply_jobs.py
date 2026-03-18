import random
import time
import os
import logging
from datetime import datetime, timedelta
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.keys import Keys
from utils import init_driver, login, save_screenshot
from rotate_headline import setup_logging, clear_debug_images
from questionnaire_handler import handle_questionnaire

APPLIED_JOBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "applied_jobs.txt")

GENERIC_TITLE_WORDS = {
    "engineer", "developer", "manager", "lead", "senior", "junior",
    "associate", "principal", "staff", "intern", "analyst", "specialist",
    "consultant", "architect", "administrator", "admin", "coordinator",
    "director", "head", "vp",
}


def build_relevance_keywords(job_titles):
    """Build keyword set from configured job titles for relevance checking.
    Keeps full title phrases and individual significant words (filtering out
    generic role-level words like 'engineer', 'senior', etc.)."""
    keywords = set()
    for title in job_titles:
        keywords.add(title.lower().strip())
    for title in job_titles:
        for word in title.lower().split():
            word = word.strip()
            if word and word not in GENERIC_TITLE_WORDS and len(word) > 2:
                keywords.add(word)
    return keywords


def is_job_relevant(text, relevance_keywords):
    """Check if text contains any relevance keyword.
    Checks longer phrases first so full-title matches are preferred."""
    text_lower = text.lower()
    for keyword in sorted(relevance_keywords, key=len, reverse=True):
        if keyword in text_lower:
            return True, keyword
    return False, None


def load_applied_jobs():
    """Load previously applied job URLs from the dedup file."""
    if not os.path.exists(APPLIED_JOBS_FILE):
        return set()
    with open(APPLIED_JOBS_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def save_applied_job(job_url):
    """Append a newly applied job URL to the dedup file."""
    with open(APPLIED_JOBS_FILE, "a", encoding="utf-8") as f:
        f.write(job_url + "\n")


def apply_for_jobs():
    """
    Searches for jobs on Naukri.com based on config in .env file and applies to them
    """
    setup_logging()
    clear_debug_images()
    
    job_titles = os.getenv("JOB_TITLES", "DevOps Engineer, Site Reliability Engineer").split(",")
    job_titles = [title.strip() for title in job_titles]
    
    locations = os.getenv("JOB_LOCATIONS", "Remote").split(",")
    locations = [location.strip() for location in locations]
    
    experience = os.getenv("JOB_EXPERIENCE", "2")
    
    applied_count = 0
    max_applications = int(os.getenv("MAX_APPLICATIONS", "3"))
    
    relevance_keywords = build_relevance_keywords(job_titles)
    logging.info(f"Relevance keywords: {relevance_keywords}")
    
    applied_jobs = load_applied_jobs()
    logging.info(f"Loaded {len(applied_jobs)} previously applied job URLs for deduplication")
    
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
        
        selected_job_title = random.choice(job_titles)
        selected_location = random.choice(locations)
        
        logging.info(f"Selected job search parameters: {selected_job_title} in {selected_location} with {experience} years experience")
        logging.info(f"Target: Apply to {max_applications} jobs")
        
        search_for_jobs(driver, selected_job_title, selected_location, experience)
        
        applied_count = process_job_listings(driver, max_applications, relevance_keywords, applied_jobs)
        
        search_attempts = 1
        max_search_attempts = 3
        
        while applied_count < max_applications and search_attempts < max_search_attempts:
            logging.info(f"Only applied to {applied_count}/{max_applications} jobs. Trying a different search...")
            search_attempts += 1
            
            selected_job_title = random.choice([title for title in job_titles if title != selected_job_title] or job_titles)
            selected_location = random.choice([loc for loc in locations if loc != selected_location] or locations)
            
            logging.info(f"New search parameters: {selected_job_title} in {selected_location} with {experience} years experience")
            
            search_for_jobs(driver, selected_job_title, selected_location, experience, fallback=True)
            
            new_applications = process_job_listings(driver, max_applications - applied_count, relevance_keywords, applied_jobs)
            applied_count += new_applications
        
        if applied_count >= max_applications:
            logging.info(f"✓ Successfully applied to {applied_count} jobs (reached target of {max_applications})")
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

def search_for_jobs(driver, job_title, location, experience, fallback=False):
    """
    Searches for jobs using the search box on Naukri.
    If fallback is True, the sort will be set to "Date" instead of "Relevance".
    """
    logging.info("Starting job search...")
    
    try:
        try:
            search_placeholder_elements = driver.find_elements(By.XPATH, 
                "//span[contains(@class, 'nI-gNb-sb__placeholder') and contains(text(), 'Search jobs here')]")
            
            if search_placeholder_elements:
                search_placeholder_elements[0].click()
                logging.info("Clicked on 'Search jobs here'")
                time.sleep(random.uniform(1.5, 3.5))
            else:
                search_icon = driver.find_element(By.CSS_SELECTOR, ".nI-gNb-sb__icon-wrapper")
                search_icon.click()
                logging.info("Clicked on search icon as fallback")
                time.sleep(random.uniform(1.5, 3.5))
                
        except Exception as e:
            logging.error(f"Failed to find search elements: {e}")
            save_screenshot(driver, "search_elements_not_found", "failure")
            
            try:
                driver.get("https://www.naukri.com/jobs-in-india")
                logging.info("Navigated directly to job search page")
                time.sleep(random.uniform(2.5, 5))
            except Exception as e:
                logging.error(f"Failed to navigate to job search page: {e}")
                save_screenshot(driver, "search_page_navigation_failed", "failure")
                raise
        
        try:
            keywords_inputs = driver.find_elements(By.XPATH, 
                "//input[@placeholder='Enter keyword / designation / companies']")
            
            if not keywords_inputs:
                keywords_inputs = driver.find_elements(By.CSS_SELECTOR, ".keywordSugg input")
                
            if keywords_inputs:
                keywords_input = keywords_inputs[0]
                keywords_input.clear()
                keywords_input.send_keys(job_title)
                keywords_input.send_keys(Keys.TAB)
                logging.info(f"Entered job title: '{job_title}'")
                time.sleep(random.uniform(1, 2.5))
            else:
                logging.error("Could not find keywords input field")
                save_screenshot(driver, "keywords_input_not_found", "failure")
        except Exception as e:
            logging.error(f"Failed to enter job title: {e}")
            save_screenshot(driver, "job_title_input_error", "failure")
        
        try:
            exp_input = driver.find_element(By.ID, "experienceDD")
            driver.execute_script("arguments[0].click();", exp_input)
            logging.info("Clicked on experience dropdown")
            time.sleep(random.uniform(1, 2.5))
            
            options = driver.find_elements(By.CSS_SELECTOR, ".dropdownMainContainer .dropdownPrimary ul li")
            
            if not options:
                options = driver.find_elements(By.XPATH, "//div[contains(@class, 'dropdownPrimary')]//li")

            if options:
                target_exp = int(experience)
                selected = False
                
                for option in options:
                    option_text = option.text.strip().lower()
                    
                    is_fresher = (target_exp == 0 and "fresher" in option_text)
                    is_match = (f"{target_exp} year" in option_text)

                    if is_fresher or is_match:
                        driver.execute_script("arguments[0].scrollIntoView(true);", option)
                        option.click()
                        selected = True
                        logging.info(f"Successfully selected experience: {option_text}")
                        break
                
                if not selected:
                    logging.warning(f"Target experience {experience} not found. Selecting first option.")
                    options[0].click()
            else:
                logging.error("No experience options found in the DOM.")
                
        except Exception as e:
            logging.error(f"Failed to select experience: {e}")
            save_screenshot(driver, "experience_selection_error", "failure")
        
        try:
            location_inputs = driver.find_elements(By.XPATH, 
                "//input[@placeholder='Enter location']")
            
            if not location_inputs:
                location_inputs = driver.find_elements(By.CSS_SELECTOR, ".locationSugg input")
                
            if location_inputs:
                location_input = location_inputs[0]
                location_input.clear()
                location_input.send_keys(location)
                location_input.send_keys(Keys.TAB)
                logging.info(f"Entered location: '{location}'")
                time.sleep(random.uniform(1, 2.5))
            else:
                logging.warning("Location input field not found")
        except Exception as e:
            logging.error(f"Failed to enter location: {e}")
            save_screenshot(driver, "location_input_error", "failure")
        
        try:
            search_buttons = driver.find_elements(By.CSS_SELECTOR, 
                ".nI-gNb-sb__icon-wrapper, button.search, input[type='submit'], button[type='submit']")
            
            if search_buttons:
                search_button = search_buttons[0]
                search_button.click()
                logging.info("Clicked search button")
                
                time.sleep(random.uniform(4, 7))
                
                try:
                    WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, 
                            ".jobTupleHeader, .cust-job-tuple, .jobTuple, div[type='tuple']"))
                    )
                    logging.info("Search results loaded successfully")
                except TimeoutException:
                    logging.warning("Timed out waiting for search results, but proceeding anyway")
                
                save_screenshot(driver, "job_search_results", "success")
            else:
                logging.error("Search button not found")
                save_screenshot(driver, "search_button_not_found", "failure")
        except Exception as e:
            logging.error(f"Failed to complete search: {e}")
            save_screenshot(driver, "search_button_click_error", "failure")
        
        try:
            freshness_dropdown_button = driver.find_element(By.ID, "filter-freshness")
            driver.execute_script("arguments[0].click();", freshness_dropdown_button)
            logging.info("Clicked on freshness dropdown")
            time.sleep(random.uniform(1, 2.5))
            
            freshness_options = driver.find_elements(By.CSS_SELECTOR, "ul[data-filter-id='freshness'] li")
            for option in freshness_options:
                if "Last 1 day" in option.text:
                    driver.execute_script("arguments[0].click();", option)
                    logging.info("Selected freshness: Last 1 day")
                    break
            else:
                logging.warning("Freshness option 'Last 1 day' not found")
        except Exception as e:
            logging.error(f"Failed to select freshness: {e}")
            save_screenshot(driver, "freshness_selection_error", "failure")
        
        try:
            sort_dropdown_button = driver.find_element(By.ID, "filter-sort")
            driver.execute_script("arguments[0].click();", sort_dropdown_button)
            logging.info("Clicked on sort dropdown")
            time.sleep(random.uniform(1, 2.5))
            
            WebDriverWait(driver, 10).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "ul[data-filter-id='sort'] li"))
            )
            
            sort_options = driver.find_elements(By.CSS_SELECTOR, "ul[data-filter-id='sort'] li")
            available_sort_options = [option.get_attribute("title") for option in sort_options]
            logging.info(f"Available sort options: {available_sort_options}")
            
            target_sort = "Date" if fallback else "Relevance"
            for option in sort_options:
                if option.get_attribute("title") == target_sort:
                    driver.execute_script("arguments[0].click();", option)
                    logging.info(f"Selected sort: {target_sort}")
                    time.sleep(random.uniform(3, 6))
                    break
            else:
                logging.warning(f"Sort option '{target_sort}' not found. Available options: {available_sort_options}")
                save_screenshot(driver, f"sort_option_not_found_{target_sort}", "failure")
        except Exception as e:
            logging.error(f"Failed to select sort: {e}")
            save_screenshot(driver, "sort_selection_error", "failure")
        
    except Exception as e:
        logging.error(f"Error in job search process: {e}")
        save_screenshot(driver, "job_search_error", "failure")

def process_job_listings(driver, max_applications, relevance_keywords, applied_jobs, page=1, max_pages=3):
    """
    Process the job listings page and apply to suitable jobs.
    Paginates up to max_pages when more relevant jobs are needed.
    """
    applied_count = 0
    tabs_opened = 0
    max_tabs_to_open = max_applications * 5
    
    try:
        job_selectors = [
            ".jobTuple", 
            ".cust-job-tuple", 
            "div[type='tuple']", 
            ".jobTupleHeader",
            "article.jobTupleHeader"
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
            try:
                next_page_buttons = driver.find_elements(By.CSS_SELECTOR, 
                    ".fright.fs14.btn-secondary.br2, a.fright, .nextPage, a[title='Next']")
                
                if next_page_buttons:
                    logging.info(f"Moving to page {page + 1} of results")
                    next_page_buttons[0].click()
                    time.sleep(random.uniform(4, 7))
                    
                    additional_applications = process_job_listings(
                        driver, max_applications - applied_count, relevance_keywords, applied_jobs,
                        page=page + 1, max_pages=max_pages
                    )
                    applied_count += additional_applications
                else:
                    logging.info("No next page button found, end of results")
            except Exception as e:
                logging.error(f"Error navigating to next page: {e}")
    
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
                try:
                    body = driver.find_element(By.TAG_NAME, "body")
                    jd_text = body.text[:3000]
                except:
                    jd_text = ""
            
            jd_relevant, matched_keyword = is_job_relevant(jd_text, relevance_keywords)
            if not jd_relevant:
                logging.info(f"⊘ Skipping irrelevant job: '{job_title}' at {company} - no keyword match in title or JD")
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
