import os
import time
import logging
import shutil
import tempfile
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Define directories for debug output
IMAGES_DIR = os.path.join(os.getcwd(), "debug", "images")
LOGS_DIR = os.path.join(os.getcwd(), "debug", "logs")

# Ensure directories exist
os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

def setup_logging():
    """
    Configures logging to write logs to a shared file and console.
    """
    log_file = os.path.join(LOGS_DIR, "naukri_bot.log")
    logging.basicConfig(
        level=logging.INFO,  # Set default log level to INFO
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()  # Optional: Logs to console
        ]
    )
    # Suppress logs from Selenium and WebDriverManager
    logging.getLogger("selenium").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("webdriver_manager").setLevel(logging.WARNING)
    logging.info("Logging configured. Logs will be written to: %s", log_file)

# Configure logging
setup_logging()

def init_driver():
    """Initialize and return a configured Chrome WebDriver instance."""
    logging.debug("Initializing WebDriver with WebDriverManager for ChromeDriver")
    
    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # Don't load images to speed up
    chrome_options.add_argument("--blink-settings=imagesEnabled=false")
    
    # Add user agent to avoid detection
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.45 Safari/537.36")
    
    # Disable all forms of cache
    chrome_options.add_argument("--disable-application-cache")
    chrome_options.add_argument("--disk-cache-size=0")
    chrome_options.add_argument("--disable-cache")
    chrome_options.add_argument("--media-cache-size=0")

    # Prevent side effects / weird Chrome memory
    chrome_options.add_argument("--disable-browser-side-navigation")
    chrome_options.add_argument("--disable-site-isolation-trials")

    # For Docker deployment, we need to use headless mode
    chrome_options.add_argument("--headless=new")
    
    # Use a temporary directory for user-data-dir
    unique_dir = f"/tmp/chrome_user_data_{int(time.time())}"
    chrome_options.add_argument(f"--user-data-dir={unique_dir}")
    logging.debug(f"Using temporary user-data-dir: {unique_dir}")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    logging.debug("WebDriver initialized successfully")
    return driver

def login(driver, max_attempts=3):
    """Login to Naukri.com with credentials from environment variables"""
    email = os.environ.get("NAUKRI_EMAIL")
    password = os.environ.get("NAUKRI_PASSWORD")
    
    if not email or not password:
        logging.error("Naukri credentials not found in environment variables")
        return False
    
    logging.info("Navigating to login page...")
    driver.get("https://www.naukri.com/nlogin/login")
    
    try:
        WebDriverWait(driver, 10).until(EC.title_contains("Login"))
        logging.debug(f"Page title: {driver.title}")
    except TimeoutException:
        logging.error("Login page did not load in time")
        save_screenshot(driver, "login_page_load_error", "failure")
        return False
    
    attempt = 1
    while attempt <= max_attempts:
        try:
            logging.info(f"Login attempt {attempt}/{max_attempts}")
            
            # Check if already logged in
            if "logout" in driver.current_url.lower() or "mnjuser/profile" in driver.current_url.lower():
                logging.info("Already logged in")
                return True
            
            # Locate and fill email field
            email_field = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//input[@id='usernameField']"))
            )
            email_field.clear()
            email_field.send_keys(email)
            logging.debug("Email entered successfully")
            
            # Locate and fill password field
            password_field = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//input[@id='passwordField']"))
            )
            password_field.clear()
            password_field.send_keys(password)
            logging.debug("Password entered successfully")
            
            # Locate and click login button
            login_button = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']"))
            )
            login_button.click()
            logging.debug("Login button clicked")
            
            # Wait for login to complete
            time.sleep(5)
            
            # Check if login was successful
            if "login" not in driver.current_url.lower():
                logging.info("Login successful")
                return True
            
            # If still on login page, check for error messages
            error_messages = driver.find_elements(By.CSS_SELECTOR, ".erLbl")
            if error_messages:
                error_text = error_messages[0].text
                logging.warning(f"Login error: {error_text}")
                save_screenshot(driver, f"login_error_attempt{attempt}", "failure")
            
            attempt += 1
            time.sleep(2)
        
        except Exception as e:
            logging.exception(f"Exception during login attempt {attempt}: {e}")
            save_screenshot(driver, f"login_error_attempt{attempt}", "failure")
            attempt += 1
            time.sleep(2)
    
    logging.error("Failed to login after multiple attempts")
    save_screenshot(driver, "login_failed_final", "failure")
    return False

def save_screenshot(driver, name, status="info"):
    """
    Take a screenshot and save it with current timestamp.
    Returns the path to the saved screenshot.
    Status can be 'success', 'failure', 'warning', or 'info'
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_dir = IMAGES_DIR
    
    # Format the filename
    filename = f"{status}_{name}_{timestamp}.png"
    screenshot_path = os.path.join(screenshot_dir, filename)
    
    try:
        # Ensure page is fully loaded and stable
        time.sleep(1)
        
        # Take full page screenshot by scrolling
        total_width = driver.execute_script("return document.body.offsetWidth")
        total_height = driver.execute_script("return document.body.scrollHeight")
        
        # Set appropriate window size
        driver.set_window_size(total_width, min(total_height, 1200))
        
        # Allow time for resizing
        time.sleep(0.5)
        
        # Save the screenshot
        driver.save_screenshot(screenshot_path)
        
        if os.path.exists(screenshot_path):
            logging.info(f"Screenshot saved: {filename}")
            return screenshot_path
        else:
            logging.error(f"Failed to save screenshot: {filename}")
            return None
            
    except Exception as e:
        logging.exception(f"Error taking screenshot {name}: {e}")
        try:
            # Fallback to basic screenshot
            driver.save_screenshot(screenshot_path)
            logging.info(f"Screenshot saved using fallback method: {filename}")
            return screenshot_path
        except:
            logging.error(f"Fallback method also failed for screenshot {name}")
            return None


