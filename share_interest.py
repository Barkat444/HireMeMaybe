import os
import time
import logging
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
from utils import init_driver, login, save_screenshot

# Set logging level to INFO
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Suppress logs from Selenium and WebDriverManager
logging.getLogger("selenium").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("webdriver_manager").setLevel(logging.WARNING)

def share_interest():
    """
    Logs in to Naukri.com and shares interest in recommended jobs up to the EARLY_ACCESS_ROLES_LIMIT.
    """
    logging.info("Starting 'Share Interest' process...")
    
    # Load environment variables
    early_access_limit = int(os.getenv("EARLY_ACCESS_ROLES_LIMIT", "2"))
    
    # Initialize the driver
    driver = init_driver()
    
    try:
        # Login to Naukri
        if not login(driver):
            logging.error("Login failed. Exiting 'Share Interest' process.")
            return
        logging.info("Logged in successfully")
        
        # Navigate to the recommended jobs page
        driver.get("https://www.naukri.com/mnjuser/recommended-earjobs")
        time.sleep(5)
        
        shared_count = 0
        while shared_count < early_access_limit:
            try:
                # Locate all job tuples
                job_tuples = driver.find_elements(By.CSS_SELECTOR, "div.tlc__tuple")
                if not job_tuples:
                    logging.warning("No job tuples found on the page")
                    break
                
                for index, job in enumerate(job_tuples):
                    if shared_count >= early_access_limit:
                        break
                    
                    try:
                        # Locate the "Share interest" button within the job tuple
                        share_button = job.find_element(By.CSS_SELECTOR, "button.unshared")
                        
                        # Scroll to the button and click it
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", share_button)
                        time.sleep(1)
                        share_button.click()
                        logging.info(f"Clicked 'Share interest' button {shared_count + 1}/{early_access_limit}")
                        shared_count += 1
                        
                        # Wait for success confirmation
                        try:
                            WebDriverWait(driver, 5).until(
                                EC.presence_of_element_located((By.XPATH, "//span[contains(text(), 'Interest shared successfully!')]"))
                            )
                            logging.info("Interest shared successfully!")
                        except TimeoutException:
                            logging.warning("No success confirmation found after clicking 'Share interest'")
                        
                        time.sleep(2)  # Small delay between clicks
                        
                        # Navigate back to the recommended jobs page
                        driver.get("https://www.naukri.com/mnjuser/recommended-earjobs")
                        time.sleep(5)
                        break  # Break to re-locate job tuples after navigating back
                    except StaleElementReferenceException:
                        logging.warning("Stale element encountered. Re-locating elements...")
                        break  # Break the loop to re-locate elements
                    except Exception as e:
                        logging.error(f"Failed to click 'Share interest' button: {e}")
                        save_screenshot(driver, f"share_interest_error_{shared_count + 1}", "failure")
                        continue
                
            except Exception as e:
                logging.error(f"Error during 'Share interest' process: {e}")
                save_screenshot(driver, "share_interest_process_error", "failure")
                break
        
        logging.info(f"✓ Successfully shared interest in {shared_count} jobs")
    
    except Exception as e:
        logging.error(f"Error in 'Share Interest' process: {e}")
        save_screenshot(driver, "share_interest_error", "failure")
    finally:
        driver.quit()
        logging.info("Browser closed")

if __name__ == "__main__":
    share_interest()