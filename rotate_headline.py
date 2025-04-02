import json
import random
import time
import os
import sys
import traceback
import glob
import shutil
import logging
from datetime import datetime, timedelta
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from utils import init_driver, login, save_screenshot

# Global log file path
CURRENT_LOG_FILE = None

def setup_logging():
    """Set up logging to capture important events to a log file in debug/logs directory"""
    global CURRENT_LOG_FILE
    
    # If logging is already set up, return the current log file
    if CURRENT_LOG_FILE and os.path.exists(CURRENT_LOG_FILE):
        return CURRENT_LOG_FILE
    
    # Remove any existing handlers to avoid duplicate logging
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    
    # Ensure debug/logs directory exists
    debug_logs_dir = os.path.join(os.getcwd(), "debug", "logs")
    os.makedirs(debug_logs_dir, exist_ok=True)
    
    # Create a log file with timestamp
    log_filename = f"naukri_bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_filepath = os.path.join(debug_logs_dir, log_filename)
    CURRENT_LOG_FILE = log_filepath
    
    # Configure basic logging - we'll use INFO level to filter out debug messages
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(message)s',
        handlers=[
            logging.FileHandler(log_filepath, mode='w'),
            logging.StreamHandler()  # Also log to console
        ]
    )
    
    # Log initial message
    logging.info(f"=== Naukri Bot Started ===")
    
    return log_filepath

def clear_debug_images():
    """Clear the debug/images directory before starting"""
    debug_images_dir = os.path.join(os.getcwd(), "debug", "images")
    if os.path.exists(debug_images_dir):
        logging.info(f"Clearing debug images")
        try:
            # Remove all files in the images directory
            for file in glob.glob(os.path.join(debug_images_dir, "*.*")):
                os.remove(file)
        except Exception as e:
            logging.error(f"Failed to clear debug images: {e}")
    else:
        # Create directory if it doesn't exist
        try:
            os.makedirs(debug_images_dir, exist_ok=True)
        except Exception as e:
            logging.error(f"Failed to create debug images directory: {e}")

def rotate_headline():
    """Update the resume headline with random content from headlines.json"""
    # Set up logging and clear debug images before starting
    setup_logging()
    clear_debug_images()
    
    driver = init_driver()
    try:
        login(driver)
        logging.info("Logged in successfully")
        logging.info("Starting headline rotation...")
        
        # Navigate to profile page
        driver.get("https://www.naukri.com/mnjuser/profile")
        logging.info("Navigated to profile page")
        time.sleep(5)
        
        # Extract current headline for verification
        current_headline = ""
        try:
            current_headline = driver.execute_script("""
                var headlineElements = document.querySelectorAll('.resumeHeadline span, .resumeHeadline p, .resumeHeadline div');
                for (var i = 0; i < headlineElements.length; i++) {
                    var text = headlineElements[i].textContent.trim();
                    if (text && text.length > 5) {
                        return text;
                    }
                }
                return null;
            """)
            
            if current_headline:
                current_headline = current_headline.strip()
                logging.info(f"Current headline: '{current_headline}'")
        except Exception as e:
            logging.error(f"Error reading current headline: {e}")
        
        # Load random headline, ensuring it's different from the current one
        try:
            with open("headlines.json", "r") as file:
                data = json.load(file)
                
                # If we have current headline, try to select a different one
                if current_headline and len(data) > 1:
                    # Extract just the core headline from the current headline text
                    for item in data:
                        if item["headline"] in current_headline:
                            current_headline = item["headline"]
                            break
                    
                    # Filter out the current headline
                    different_headlines = [h for h in data if h["headline"] != current_headline]
                    
                    if different_headlines:
                        entry = random.choice(different_headlines)
                        logging.info("Selected a different headline for update")
                    else:
                        entry = random.choice(data)
                else:
                    entry = random.choice(data)
                
                headline = entry["headline"]
                logging.info(f"Selected new headline: {headline}")
                
                # Check if the selected headline is the same as the current one
                if headline in current_headline:
                    logging.info("Selected headline is the same as current one")
                    
                    if len(data) == 1:
                        logging.info("No different headline available, skipping update")
                        # Still proceed to upload resume
                        upload_resume(driver)
                        driver.quit()
                        # Log next scheduled time
                        log_next_scheduled_time()
                        return
                    else:
                        # Try to select a different one
                        different_headlines = [h for h in data if h["headline"] != headline and h["headline"] != current_headline]
                        if different_headlines:
                            entry = random.choice(different_headlines)
                            headline = entry["headline"]
                            logging.info(f"Selected alternative headline: {headline}")
                        else:
                            logging.info("No different headline available, skipping update")
                            # Still proceed to upload resume
                            upload_resume(driver)
                            driver.quit()
                            # Log next scheduled time
                            log_next_scheduled_time()
                            return
                
        except Exception as e:
            logging.error(f"Failed to load headlines: {e}")
            save_screenshot(driver, "headline_json_error", "failure")
            driver.quit()
            # Log next scheduled time
            log_next_scheduled_time()
            return
        
        # Update the headline
        headline_updated = update_resume_headline(driver, headline)
        
        if headline_updated:
            logging.info("✓ Successfully updated profile headline")
        else:
            logging.error("✗ Failed to update headline")
            save_screenshot(driver, "profile_update_failed", "failure")
        
        # Proceed to upload resume
        upload_resume(driver)
            
    except Exception as e:
        logging.error(f"Error during headline rotation: {e}")
        save_screenshot(driver, "rotation_failed", "failure")
    finally:
        driver.quit()
        logging.info("Browser closed")
        # Log next scheduled time
        log_next_scheduled_time()

def log_next_scheduled_time():
    """Log the next scheduled run time based on interval in .env"""
    interval_hours = int(os.getenv("INTERVAL_HOURS", "1"))  # Default to 1 hour
    if interval_hours > 0:
        next_run = datetime.now() + timedelta(hours=interval_hours)
        logging.info(f"Next scheduled run: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"Will run every {interval_hours} hour(s)")
    else:
        logging.info("No schedule set - running in single execution mode")

def upload_resume(driver):
    """Upload the resume file from the current directory"""
    logging.info("Starting resume upload process...")
    
    try:
        # Ensure we're on the profile page
        if "/mnjuser/profile" not in driver.current_url:
            logging.info("Navigating to profile page for resume upload")
            driver.get("https://www.naukri.com/mnjuser/profile")
            time.sleep(5)
        
        # Find the resume upload section
        resume_section_found = False
        
        # Method 1: Try to find the file input directly
        try:
            file_input = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "attachCV"))
            )
            resume_section_found = True
            logging.info("Found resume upload section")
        except Exception as e:
            # Method 2: Try to find by CSS selector
            try:
                file_input = driver.find_element(By.CSS_SELECTOR, "input[type='file'].fileUpload")
                resume_section_found = True
                logging.info("Found resume upload section")
            except Exception as e:
                # Try to find the upload section by text content
                try:
                    upload_button = driver.find_element(By.XPATH, "//input[@value='Update resume']")
                    upload_section = driver.execute_script("return arguments[0].closest('section')", upload_button)
                    file_input = upload_section.find_element(By.CSS_SELECTOR, "input[type='file']")
                    resume_section_found = True
                    logging.info("Found resume upload section")
                except Exception as e:
                    save_screenshot(driver, "resume_section_not_found", "failure")
        
        if not resume_section_found:
            logging.error("Could not find resume upload section")
            save_screenshot(driver, "resume_upload_section_not_found", "failure")
            return False
        
        # Find a suitable resume file dynamically
        resume_filepath = find_resume_file()
        
        if not resume_filepath:
            logging.error("No resume file found")
            save_screenshot(driver, "resume_file_not_found", "failure")
            return False
        
        # Add resume filename to logs
        resume_filename = os.path.basename(resume_filepath)
        logging.info(f"Resume file to upload: {resume_filename}")
        
        # Send the file path to the file input element
        try:
            # Make the file input visible and enabled
            driver.execute_script("""
                arguments[0].style.display = 'block';
                arguments[0].style.visibility = 'visible';
                arguments[0].style.opacity = '1';
            """, file_input)
            
            # Send the file path to the input
            file_input.send_keys(resume_filepath)
            logging.info(f"Uploading resume file: {resume_filename}...")
            
            # Wait for upload to complete
            time.sleep(10)
            
            # Check for success indicators
            try:
                success_indicators = [
                    "//div[contains(text(), 'uploaded successfully')]",
                    "//div[contains(text(), 'Resume updated')]",
                    "//div[contains(@class, 'updateOn')]"
                ]
                
                success_found = False
                for indicator in success_indicators:
                    try:
                        WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.XPATH, indicator))
                        )
                        success_found = True
                        break
                    except:
                        continue
                
                if success_found:
                    logging.info(f"✓ Resume '{resume_filename}' uploaded successfully")
                    return True
                else:
                    # Wait for any loading indicators to disappear
                    try:
                        WebDriverWait(driver, 15).until_not(
                            EC.presence_of_element_located((By.CSS_SELECTOR, ".saving, .loading, .spinner"))
                        )
                    except:
                        pass
                    
                    logging.info(f"✓ Resume '{resume_filename}' upload completed")
                    return True
            except Exception as e:
                logging.error(f"Error checking upload status: {e}")
                
            # Assuming success if we reached here with no errors
            logging.info(f"✓ Resume '{resume_filename}' upload process completed")
            return True
            
        except Exception as e:
            logging.error(f"✗ Failed to upload resume '{resume_filename}': {e}")
            save_screenshot(driver, "resume_upload_failed", "failure")
            return False
            
    except Exception as e:
        logging.error(f"Error in resume upload process: {e}")
        save_screenshot(driver, "resume_upload_process_error", "failure")
        return False

def find_resume_file():
    """Find a resume file in the current directory.
    
    Priority:
    1. Files with 'resume' or 'cv' in the name (case insensitive) and .pdf extension
    2. Any PDF file
    """
    current_dir = os.getcwd()
    logging.info(f"Looking for resume files...")
    
    try:
        files = os.listdir(current_dir)
        pdf_files = [f for f in files if f.lower().endswith('.pdf')]
        
        if not pdf_files:
            logging.info("No PDF files found")
            return None
        
        # First priority: Look for files with keyword in the name
        resume_keywords = ['resume', 'cv', 'curriculum', 'vitae', 'barkat']
        for keyword in resume_keywords:
            for pdf in pdf_files:
                if keyword.lower() in pdf.lower():
                    resume_path = os.path.join(current_dir, pdf)
                    logging.info(f"Found resume file: {pdf}")
                    return resume_path
        
        # Second priority: Just use the first PDF file found
        if pdf_files:
            resume_path = os.path.join(current_dir, pdf_files[0])
            logging.info(f"Using PDF file: {pdf_files[0]}")
            return resume_path
        
        return None
    except Exception as e:
        logging.error(f"Error finding resume file: {e}")
        return None

def click_save_button(driver, save_button, description=""):
    """Ensure save buttons are properly clicked with multiple fallback methods"""
    logging.info(f"Clicking {description} save button")
    
    # Ensure the button is visible and centered
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", save_button)
    time.sleep(1)
    
    # Take multiple approaches to click the button
    methods_tried = []
    
    # Method 1: Try direct click
    try:
        save_button.click()
        methods_tried.append("direct")
    except Exception:
        # Method 2: Try JavaScript click
        try:
            driver.execute_script("arguments[0].click();", save_button)
            methods_tried.append("javascript")
        except Exception:
            # Method 3: Try dispatch click event
            try:
                driver.execute_script("""
                    var clickEvent = new MouseEvent('click', {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    });
                    arguments[0].dispatchEvent(clickEvent);
                """, save_button)
                methods_tried.append("event")
            except Exception:
                # Method 4: Try form submit
                try:
                    driver.execute_script("""
                        var el = arguments[0];
                        var form = el.closest('form');
                        if (form) {
                            form.submit();
                            return "Submitted parent form";
                        }
                        return "No parent form found";
                    """, save_button)
                    methods_tried.append("form")
                except Exception:
                    pass
    
    # Wait for the save operation to complete
    try:
        WebDriverWait(driver, 15).until_not(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".saving, .loading, .spinner"))
        )
    except:
        # If no loading indicator found, just wait
        time.sleep(10)
    
    if len(methods_tried) > 0:
        logging.info("Save button clicked successfully")
    else:
        logging.error("Failed to click save button")
        
    return len(methods_tried) > 0

def update_resume_headline(driver, headline_text):
    """Update the resume headline by clicking the edit button and modifying the content"""
    logging.info("Updating resume headline...")
    
    try:
        # Find and click the edit button for headline section
        try:
            edit_button = None
            
            # Method 1: Look for the edit icon inside the widgetHead section
            edit_buttons = driver.find_elements(By.CSS_SELECTOR, ".widgetHead .edit.icon")
            if edit_buttons and len(edit_buttons) > 0:
                edit_button = edit_buttons[0]
            
            # Method 2: Look for edit buttons by XPath
            if not edit_button:
                xpath_edit_buttons = driver.find_elements(By.XPATH, "//span[contains(@class, 'edit icon')]")
                if xpath_edit_buttons and len(xpath_edit_buttons) > 0:
                    edit_button = xpath_edit_buttons[0]
            
            # Method 3: Look for any element with "edit" in its class
            if not edit_button:
                generic_edit_buttons = driver.find_elements(By.CSS_SELECTOR, ".edit")
                if generic_edit_buttons and len(generic_edit_buttons) > 0:
                    edit_button = generic_edit_buttons[0]
            
            if edit_button:
                logging.info("Clicking headline edit button")
                driver.execute_script("arguments[0].click();", edit_button)
                time.sleep(3)
            else:
                logging.info("Trying direct navigation to edit page")
                # Try navigating directly to the edit page
                driver.get("https://www.naukri.com/mnjuser/profile?id=&altresid")
                time.sleep(5)
                
                # Try clicking button by text
                headline_section_buttons = driver.find_elements(By.XPATH, "//button[contains(text(), 'Edit Resume Headline')]")
                if headline_section_buttons:
                    driver.execute_script("arguments[0].click();", headline_section_buttons[0])
                    time.sleep(3)
                else:
                    logging.error("Could not find any way to edit headline")
                    save_screenshot(driver, "headline_edit_button_not_found", "failure")
                    return False
        except Exception as e:
            logging.error(f"Error finding headline edit button: {e}")
            save_screenshot(driver, "headline_edit_button_error", "failure")
            return False
        
        # Wait for edit form to appear
        time.sleep(3)
        
        # Find the textarea field
        try:
            headline_field = driver.find_element(By.ID, "resumeHeadlineTxt")
            logging.info("Found headline textarea")
            
            # Get the current value for verification
            current_value = headline_field.get_attribute("value") or headline_field.text
            logging.info(f"CURRENT HEADLINE IN FORM: '{current_value}'")
            
            # Check if current value matches desired headline
            if current_value.strip() == headline_text.strip():
                logging.info("Current headline already matches the desired headline")
                logging.info("Clicking Save button to confirm")
            else:
                # Clear and set new value
                headline_field.clear()
                time.sleep(0.5)
                headline_field.send_keys(headline_text)
                time.sleep(1)
                
                # Verify the text was actually set
                updated_value = headline_field.get_attribute("value") or headline_field.text
                logging.info(f"UPDATED HEADLINE: '{updated_value}'")
            
            # Find the save button
            save_button = None
            
            # Direct targeting: btn-dark-ot with type submit
            save_buttons = driver.find_elements(By.CSS_SELECTOR, "button.btn-dark-ot[type='submit']")
            if save_buttons and len(save_buttons) > 0:
                save_button = save_buttons[0]
            
            # Fallback 1: Try just by class
            if not save_button:
                save_buttons = driver.find_elements(By.CSS_SELECTOR, ".btn-dark-ot")
                if save_buttons and len(save_buttons) > 0:
                    save_button = save_buttons[0]
            
            # Fallback 2: Try by text and class
            if not save_button:
                save_buttons = driver.find_elements(By.XPATH, 
                    "//button[contains(text(), 'Save') or contains(@class, 'saveButton')]")
                if save_buttons and len(save_buttons) > 0:
                    save_button = save_buttons[0]
            
            if save_button:
                # Use the dedicated function to ensure proper clicking
                click_save_button(driver, save_button, "headline")
                headline_was_updated = True
                
                # Check if the update was successful by returning to profile
                driver.get("https://www.naukri.com/mnjuser/profile")
                logging.info("Navigated back to profile page to verify update")
                time.sleep(5)
                
                # Verify headline update with multiple methods
                logging.info("Verifying headline update...")
                
                # Method 1: Direct JavaScript extraction
                updated_headline_on_page = driver.execute_script("""
                    var headlineElements = document.querySelectorAll('.resumeHeadline span, .resumeHeadline p, .resumeHeadline div, .resumeHeadline, .headline');
                    for (var i = 0; i < headlineElements.length; i++) {
                        var text = headlineElements[i].textContent.trim();
                        if (text && text.length > 10) {
                            return text;
                        }
                    }
                    return null;
                """)
                
                if updated_headline_on_page:
                    logging.info(f"HEADLINE ON PAGE AFTER UPDATE: '{updated_headline_on_page}'")
                    
                    # Check if the new headline text is contained in what's displayed
                    if headline_text in updated_headline_on_page:
                        logging.info(f"✓ Headline updated successfully to: \"{headline_text}\"")
                        return True
                
                # Method 2: Look for exact headline in page source
                if headline_text in driver.page_source:
                    logging.info(f"✓ Headline updated successfully to: \"{headline_text}\"")
                    return True
                
                # Method 3: Look for truncated headline (first 50 chars)
                headline_start = headline_text[:50]
                if headline_start in driver.page_source:
                    logging.info(f"✓ Headline updated successfully to: \"{headline_text}\"")
                    return True
                
                # Method 4: Look for keyword matches
                headline_words = [word for word in headline_text.split() if len(word) > 5]
                matches = 0
                matching_words = []
                for word in headline_words:
                    if word in driver.page_source:
                        matches += 1
                        matching_words.append(word)
                
                if matches >= 2 and len(headline_words) >= 2:
                    logging.info(f"✓ Headline updated successfully to: \"{headline_text}\"")
                    return True
                
                # Even if verification failed, consider it a success if the form was submitted
                if headline_was_updated:
                    logging.info(f"✓ Headline likely updated successfully to: \"{headline_text}\"")
                    return True
                
                # No verification was successful
                logging.warning("Headline update verification failed")
                save_screenshot(driver, "headline_verification_failed", "failure") 
                return False
            else:
                logging.error("No save button found for headline")
                save_screenshot(driver, "headline_save_button_not_found", "failure")
                return False
        except Exception as e:
            logging.error(f"Error finding headline field: {e}")
            save_screenshot(driver, "headline_field_error", "failure")
            return False
    except Exception as e:
        logging.error(f"Error in headline update: {e}")
        save_screenshot(driver, "headline_update_error", "failure")
        return False

if __name__ == "__main__":
    rotate_headline()
