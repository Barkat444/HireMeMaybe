import json
import random
import time
import os
import sys
import traceback
import re
import logging
import pathlib
import subprocess
from datetime import datetime, timedelta
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.keys import Keys
from utils import init_driver, login, save_screenshot
from rotate_headline import setup_logging, clear_debug_images

def apply_for_jobs():
    """
    Searches for jobs on Naukri.com based on config in .env file and applies to them
    """
    # Set up logging and clear debug images before starting
    setup_logging()
    clear_debug_images()
    
    # Load environment variables for job search
    job_titles = os.getenv("JOB_TITLES", "DevOps Engineer, Site Reliability Engineer").split(",")
    job_titles = [title.strip() for title in job_titles]
    
    locations = os.getenv("JOB_LOCATIONS", "Remote").split(",")
    locations = [location.strip() for location in locations]
    
    experience = os.getenv("JOB_EXPERIENCE", "2")
    
    applied_count = 0
    max_applications = int(os.getenv("MAX_APPLICATIONS", "3"))
    
    # Initialize the driver
    driver = init_driver()
    
    try:
        # Login to Naukri
        logging.info("Attempting to log in to Naukri.com")
        if not login(driver):
            logging.error("Login failed. Exiting job application process.")
            return 0
        logging.info("Logged in successfully")
        
        # Navigate to profile page where we'll start the job search
        driver.get("https://www.naukri.com/mnjuser/profile")
        logging.info("Navigated to profile page")
        time.sleep(5)
        
        # Select random job title and location from the configured lists
        selected_job_title = random.choice(job_titles)
        selected_location = random.choice(locations)
        
        logging.info(f"Selected job search parameters: {selected_job_title} in {selected_location} with {experience} years experience")
        logging.info(f"Target: Apply to {max_applications} jobs")
        
        # First search
        search_for_jobs(driver, selected_job_title, selected_location, experience)
        
        # Process job listings and apply until we reach max_applications
        applied_count = process_job_listings(driver, max_applications)
        
        # If we haven't reached max_applications, try fallback searches
        search_attempts = 1
        max_search_attempts = 3
        
        while applied_count < max_applications and search_attempts < max_search_attempts:
            logging.info(f"Only applied to {applied_count}/{max_applications} jobs. Trying a different search...")
            search_attempts += 1
            
            # Select a different job title and location
            selected_job_title = random.choice([title for title in job_titles if title != selected_job_title] or job_titles)
            selected_location = random.choice([loc for loc in locations if loc != selected_location] or locations)
            
            logging.info(f"New search parameters: {selected_job_title} in {selected_location} with {experience} years experience")
            
            # Re-select experience and search again with new parameters (fallback=True)
            search_for_jobs(driver, selected_job_title, selected_location, experience, fallback=True)
            
            # Process additional listings
            new_applications = process_job_listings(driver, max_applications - applied_count)
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
        
        # Log next run information
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
        # Find and click on "Search jobs here" placeholder
        try:
            # Try first with the placeholder
            search_placeholder_elements = driver.find_elements(By.XPATH, 
                "//span[contains(@class, 'nI-gNb-sb__placeholder') and contains(text(), 'Search jobs here')]")
            
            if search_placeholder_elements:
                search_placeholder_elements[0].click()
                logging.info("Clicked on 'Search jobs here'")
                time.sleep(2)
            else:
                # Try with the search icon
                search_icon = driver.find_element(By.CSS_SELECTOR, ".nI-gNb-sb__icon-wrapper")
                search_icon.click()
                logging.info("Clicked on search icon as fallback")
                time.sleep(2)
                
        except Exception as e:
            logging.error(f"Failed to find search elements: {e}")
            save_screenshot(driver, "search_elements_not_found", "failure")
            
            # Try a different approach - go directly to search page
            try:
                driver.get("https://www.naukri.com/jobs-in-india")
                logging.info("Navigated directly to job search page")
                time.sleep(3)
            except Exception as e:
                logging.error(f"Failed to navigate to job search page: {e}")
                save_screenshot(driver, "search_page_navigation_failed", "failure")
                raise
        
        # Fill in the job title in the keywords input
        try:
            keywords_inputs = driver.find_elements(By.XPATH, 
                "//input[@placeholder='Enter keyword / designation / companies']")
            
            if not keywords_inputs:
                keywords_inputs = driver.find_elements(By.CSS_SELECTOR, ".keywordSugg input")
                
            if keywords_inputs:
                keywords_input = keywords_inputs[0]
                keywords_input.clear()
                keywords_input.send_keys(job_title)
                keywords_input.send_keys(Keys.TAB)  # Tab out of the field
                logging.info(f"Entered job title: '{job_title}'")
                time.sleep(1)
            else:
                logging.error("Could not find keywords input field")
                save_screenshot(driver, "keywords_input_not_found", "failure")
        except Exception as e:
            logging.error(f"Failed to enter job title: {e}")
            save_screenshot(driver, "job_title_input_error", "failure")
        
        # Select experience from dropdown
        try:
            # Locate the experience dropdown
            exp_dropdown = driver.find_element(By.XPATH, "//input[@placeholder='Select experience']")
            driver.execute_script("arguments[0].click();", exp_dropdown)  # Open the dropdown
            logging.info("Clicked on experience dropdown")
            time.sleep(2)
            
            # Wait for dropdown options to appear
            exp_options = driver.find_elements(By.CSS_SELECTOR, ".dropdownPrimary ul li")
            
            if exp_options:
                # Normalize the target experience value
                target_exp = int(experience)
                target_text = f"{target_exp} years" if target_exp > 1 else f"{target_exp} year"
                target_text = target_text.lower()  # Normalize to lowercase
                
                selected = False
                for option in exp_options:
                    option_text = option.text.strip().lower()  # Normalize option text
                    if option_text == "fresher" and target_exp == 0:
                        driver.execute_script("arguments[0].click();", option)
                        selected = True
                        logging.info(f"Selected experience: {option_text}")
                        break
                    elif option_text == target_text:
                        driver.execute_script("arguments[0].click();", option)
                        selected = True
                        logging.info(f"Selected experience: {option_text}")
                        break
                
                if not selected:
                    logging.error(f"Experience value '{target_text}' not found in dropdown options: {[opt.text.strip() for opt in exp_options]}")
                    # Fallback to the first option
                    driver.execute_script("arguments[0].click();", exp_options[0])
                    logging.warning(f"Selected first available option: {exp_options[0].text.strip()}")
            else:
                logging.warning("No experience options found in dropdown")
        except Exception as e:
            logging.error(f"Failed to select experience: {e}")
            save_screenshot(driver, "experience_selection_error", "failure")
            # Continue even if experience selection fails
        
        # Fill in the location
        try:
            location_inputs = driver.find_elements(By.XPATH, 
                "//input[@placeholder='Enter location']")
            
            if not location_inputs:
                location_inputs = driver.find_elements(By.CSS_SELECTOR, ".locationSugg input")
                
            if location_inputs:
                location_input = location_inputs[0]
                location_input.clear()
                location_input.send_keys(location)
                location_input.send_keys(Keys.TAB)  # Tab out of the field
                logging.info(f"Entered location: '{location}'")
                time.sleep(1)
            else:
                logging.warning("Location input field not found")
        except Exception as e:
            logging.error(f"Failed to enter location: {e}")
            save_screenshot(driver, "location_input_error", "failure")
        
        # Click the search button
        try:
            search_buttons = driver.find_elements(By.CSS_SELECTOR, 
                ".nI-gNb-sb__icon-wrapper, button.search, input[type='submit'], button[type='submit']")
            
            if search_buttons:
                search_button = search_buttons[0]
                search_button.click()
                logging.info("Clicked search button")
                
                # Wait for search results to load
                time.sleep(5)  # Give some time for the page to start loading
                
                try:
                    WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, 
                            ".jobTupleHeader, .cust-job-tuple, .jobTuple, div[type='tuple']"))
                    )
                    logging.info("Search results loaded successfully")
                except TimeoutException:
                    logging.warning("Timed out waiting for search results, but proceeding anyway")
                
                # Take a screenshot of search results
                save_screenshot(driver, "job_search_results", "success")
            else:
                logging.error("Search button not found")
                save_screenshot(driver, "search_button_not_found", "failure")
        except Exception as e:
            logging.error(f"Failed to complete search: {e}")
            save_screenshot(driver, "search_button_click_error", "failure")
        
        # Select freshness from dropdown
        try:
            # Locate the freshness dropdown button
            freshness_dropdown_button = driver.find_element(By.ID, "filter-freshness")
            driver.execute_script("arguments[0].click();", freshness_dropdown_button)  # Open the dropdown
            logging.info("Clicked on freshness dropdown")
            time.sleep(1)
            
            # Locate and select the "Last 1 day" option
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
        
        # Select sort option based on fallback flag
        try:
            # Locate the sort dropdown button
            sort_dropdown_button = driver.find_element(By.ID, "filter-sort")
            driver.execute_script("arguments[0].click();", sort_dropdown_button)  # Open the dropdown
            logging.info("Clicked on sort dropdown")
            time.sleep(1)
            
            # Wait for the sort options to load
            WebDriverWait(driver, 10).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "ul[data-filter-id='sort'] li"))
            )
            
            # Dynamically re-locate the sort options to avoid stale element reference
            sort_options = driver.find_elements(By.CSS_SELECTOR, "ul[data-filter-id='sort'] li")
            available_sort_options = [option.get_attribute("title") for option in sort_options]
            logging.info(f"Available sort options: {available_sort_options}")
            
            # Select the target sort option
            target_sort = "Date" if fallback else "Relevance"
            for option in sort_options:
                if option.get_attribute("title") == target_sort:
                    driver.execute_script("arguments[0].click();", option)
                    logging.info(f"Selected sort: {target_sort}")
                    time.sleep(4)
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

def process_job_listings(driver, max_applications):
    """
    Process the job listings page and apply to suitable jobs
    """
    applied_count = 0
    processed_jobs = 0
    max_jobs_to_process = max_applications * 5  # Process up to 5x the target to find enough applicable jobs
    
    try:
        # Find all job listings
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
                logging.info(f"Found {len(listings)} job listings with selector '{selector}'")
                break
        
        if not job_listings:
            logging.error("No job listings found on page")
            save_screenshot(driver, "no_job_listings", "failure")
            return 0
        
        logging.info(f"Found {len(job_listings)} job listings, targeting {max_applications} applications")
    
        # Process each job listing
        for index, job in enumerate(job_listings):
            if applied_count >= max_applications:
                logging.info(f"✓ Reached target application limit ({max_applications})")
                break
                
            if processed_jobs >= max_jobs_to_process:
                # Check if EARLY_ACCESS_ROLES is enabled in the .env file
                early_access_roles = os.getenv("EARLY_ACCESS_ROLES", "false").strip().lower() == "true"
                if early_access_roles:
                    # Run the share_interest.py script before logging the limit message
                    try:
                        logging.info("Running share_interest.py script before reaching processing limit")
                        current_dir = pathlib.Path(__file__).parent.resolve()
                        result = subprocess.run(
                            ["python3", f"{current_dir}/share_interest.py"],
                            capture_output=True,
                            text=True
                        )
                        logging.info(result.stdout)
                        if result.stderr:
                            logging.error(result.stderr)
                    except Exception as e:
                        logging.error(f"Error running share_interest.py script: {e}")
                
                logging.info(f"Reached maximum job processing limit ({max_jobs_to_process})")
                break
                
            processed_jobs += 1
            
            try:
                # Extract job details for logging
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
                
                # Get the current window handle
                main_window = driver.current_window_handle
                
                # Click on the job title to open it in a new tab
                job_link = job_title_element.get_attribute("href")
                
                if not job_link:
                    logging.warning(f"No link found for job {index+1}, skipping")
                    continue
                
                # Open in new tab
                driver.execute_script(f"window.open('{job_link}', '_blank');")
                
                # Switch to the new tab
                time.sleep(3)
                windows = driver.window_handles
                driver.switch_to.window(windows[-1])
                
                logging.info(f"Opened job details in new tab: {job_title}")
                
                # Check for direct apply button
                apply_result = check_and_apply(driver, job_title, company)
                
                if apply_result:
                    applied_count += 1
                    logging.info(f"Progress: Applied to {applied_count}/{max_applications} jobs")
                    
                # Close the current tab and switch back to main window
                driver.close()
                driver.switch_to.window(main_window)
                logging.info("Returned to job listings page")
                
                # Small delay between job applications
                time.sleep(random.uniform(2, 4))
                
            except Exception as e:
                logging.error(f"Error processing job listing: {e}")
                save_screenshot(driver, f"job_listing_error_{index}", "failure")
                
                # Make sure we're back on the main window
                try:
                    driver.switch_to.window(main_window)
                except:
                    pass
                
                continue
        
        # If we've gone through all jobs on the page but haven't reached our target,
        # check if there are next page buttons
        if applied_count < max_applications and processed_jobs < len(job_listings):
            try:
                next_page_buttons = driver.find_elements(By.CSS_SELECTOR, 
                    ".fright.fs14.btn-secondary.br2, a.fright, .nextPage, a[title='Next']")
                
                if next_page_buttons:
                    logging.info("Moving to next page of results")
                    next_page_buttons[0].click()
                    time.sleep(5)
                    
                    # Process additional jobs on the next page
                    additional_applications = process_job_listings(driver, max_applications - applied_count)
                    applied_count += additional_applications
            except Exception as e:
                logging.error(f"Error navigating to next page: {e}")
    
    except Exception as e:
        logging.error(f"Error finding job listings: {e}")
        save_screenshot(driver, "job_listings_error", "failure")
    
    return applied_count

def check_and_apply(driver, job_title, company):
    """
    Check if the job has a direct apply button, and if so, apply for it
    """
    try:
        # Wait for page to load
        time.sleep(3)
        
        # Take screenshot of job details
        screenshot_path = save_screenshot(driver, f"job_details_{job_title.replace(' ', '_')[:20]}", "info")
        logging.info(f"Screenshot saved: {screenshot_path}")
        
        # Check for "Apply on company site" button - we want to skip these
        company_site_buttons = driver.find_elements(By.XPATH, 
            "//*[contains(text(), 'Apply on company site') or contains(text(), 'Apply on Company Site')]")
        
        if company_site_buttons:
            logging.info(f"Job at {company} requires applying on company site - skipping")
            screenshot_path = save_screenshot(driver, f"skipped_company_site_{company.replace(' ', '_')[:20]}", "info")
            logging.info(f"Company site application screenshot saved: {screenshot_path}")
            return False
            
        # Find the apply button - there are multiple variations of how this appears
        apply_buttons = []
        
        # Look for common apply button selectors
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
            
        # Click the first available apply button
        apply_button = apply_buttons[0]
        logging.info(f"Found Apply button for job at {company}")
        
        # Scroll to the button to make it visible
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", apply_button)
        time.sleep(1)
        
        # Take screenshot before clicking apply
        screenshot_path = save_screenshot(driver, f"before_apply_{company.replace(' ', '_')[:20]}", "info")
        logging.info(f"Before apply screenshot saved: {screenshot_path}")
        
        # Click apply button
        apply_button.click()
        logging.info(f"Clicked Apply button for job at {company}")
        time.sleep(5)  # Extended wait time to ensure page loads
        
        # Take screenshot after clicking apply
        screenshot_path = save_screenshot(driver, f"after_apply_click_{company.replace(' ', '_')[:20]}", "info")
        logging.info(f"After apply click screenshot saved: {screenshot_path}")
        
        # Check for success message
        success = False
        
        # Common success message patterns
        success_patterns = [
            "You have successfully applied",
            "Application successful",
            "Applied successfully",
            "You have already applied",
            "Application confirmed",
            "successfully applied to",
            "Successfully applied"
        ]
        
        # Check for success message in page source
        page_text = driver.page_source.lower()
        matched_pattern = None
        
        for pattern in success_patterns:
            if pattern.lower() in page_text:
                success = True
                matched_pattern = pattern
                logging.info(f"✓ Found success message: '{pattern}' for job at {company}")
                screenshot_path = save_screenshot(driver, f"application_success_{company.replace(' ', '_')[:20]}", "success")
                logging.info(f"Application success screenshot saved: {screenshot_path}")
                break
                
        # If not found in page source, check for visible success elements
        if not success:
            success_messages = driver.find_elements(By.XPATH, 
                "//*[contains(text(), 'successfully applied') or contains(text(), 'Successfully applied')]")
            
            if success_messages:
                success = True
                message_text = success_messages[0].text.strip()
                matched_pattern = message_text
                logging.info(f"✓ Found visible success message: '{message_text}' for job at {company}")
                
                # Scroll to the success message for better screenshot
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", success_messages[0])
                time.sleep(1)
                
                screenshot_path = save_screenshot(driver, f"application_success_visible_{company.replace(' ', '_')[:20]}", "success")
                logging.info(f"Visible success screenshot saved: {screenshot_path}")
                
        # Check if there's a confirmation dialog or popup that needs additional handling
        if not success:
            # Look for any popups or forms that might need to be submitted
            try:
                # Handle common popups and dialogs
                dialogs = driver.find_elements(By.CSS_SELECTOR, 
                    ".modal, .popup, .dialog, .overlay, [role='dialog']")
                
                if dialogs:
                    logging.info(f"Found dialog/popup when applying to job at {company}")
                    screenshot_path = save_screenshot(driver, f"application_dialog_{company.replace(' ', '_')[:20]}", "info")
                    logging.info(f"Dialog screenshot saved: {screenshot_path}")
                    
                    # Try to find a submit or confirm button in the dialog
                    for dialog in dialogs:
                        confirm_buttons = dialog.find_elements(By.XPATH, 
                            ".//button[contains(text(), 'Submit') or contains(text(), 'Confirm') or contains(text(), 'Apply') or contains(text(), 'OK')]")
                        
                        if confirm_buttons:
                            # Highlight the button
                            driver.execute_script("arguments[0].style.border='3px solid red'", confirm_buttons[0])
                            time.sleep(1)
                            
                            screenshot_path = save_screenshot(driver, f"before_dialog_confirmation_{company.replace(' ', '_')[:20]}", "info")
                            logging.info(f"Before dialog confirmation screenshot saved: {screenshot_path}")
                            
                            confirm_buttons[0].click()
                            logging.info(f"Clicked confirmation button in dialog for job at {company}")
                            time.sleep(5)  # Extended wait time to ensure page loads
                            
                            # Take screenshot after confirmation
                            screenshot_path = save_screenshot(driver, f"after_dialog_confirmation_{company.replace(' ', '_')[:20]}", "info")
                            logging.info(f"After dialog confirmation screenshot saved: {screenshot_path}")
                            
                            # Check again for success message
                            page_text = driver.page_source.lower()
                            for pattern in success_patterns:
                                if pattern.lower() in page_text:
                                    success = True
                                    matched_pattern = pattern
                                    logging.info(f"✓ Found success message after confirmation: '{pattern}' for job at {company}")
                                    screenshot_path = save_screenshot(driver, f"application_success_confirmed_{company.replace(' ', '_')[:20]}", "success")
                                    logging.info(f"Success after confirmation screenshot saved: {screenshot_path}")
                                    break
                                    
                            # If still not successful, check for visible success elements again
                            if not success:
                                success_messages = driver.find_elements(By.XPATH, 
                                    "//*[contains(text(), 'successfully applied') or contains(text(), 'Successfully applied')]")
                                
                                if success_messages:
                                    success = True
                                    message_text = success_messages[0].text.strip()
                                    matched_pattern = message_text
                                    logging.info(f"✓ Found visible success message after confirmation: '{message_text}' for job at {company}")
                                    
                                    # Scroll to the success message for better screenshot
                                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", success_messages[0])
                                    time.sleep(1)
                                    
                                    screenshot_path = save_screenshot(driver, f"application_success_visible_confirmed_{company.replace(' ', '_')[:20]}", "success")
                                    logging.info(f"Visible success after confirmation screenshot saved: {screenshot_path}")
                                    
            except Exception as e:
                logging.error(f"Error handling application confirmation: {e}")
                screenshot_path = save_screenshot(driver, f"confirmation_error_{company.replace(' ', '_')[:20]}", "failure")
                logging.info(f"Confirmation error screenshot saved: {screenshot_path}")
        
        # Final decision and logging
        if success:
            logging.info(f"✅ SUCCESSFULLY APPLIED TO JOB: {job_title} at {company}")
            if matched_pattern:
                logging.info(f"✅ Success message: '{matched_pattern}'")
            
            # Take final screenshot with success status clearly visible
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
            time.sleep(1)
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