import schedule
import time
import os
import traceback
import logging
from datetime import datetime, timedelta
from rotate_headline import rotate_headline, clear_debug_images, setup_logging
from apply_jobs import apply_for_jobs
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

def job():
    try:
        # Set up logging and clear debug images before starting
        log_file = setup_logging()
        clear_debug_images()
        
        start_time = datetime.now()
        logging.info(f"Starting scheduled tasks at {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"User account: {os.getenv('NAUKRI_EMAIL')}")
        
        # Run headline rotation if enabled
        if os.getenv("RUN_SUMMARY_ROTATION", "true").lower() == "true":
            logging.info("Running headline rotation task")
            rotate_headline()
            logging.info("Headline rotation task completed")
        else:
            logging.info("Headline rotation is disabled in settings")
        
        # Run job application if enabled
        if os.getenv("RUN_JOB_APPLICATIONS", "false").lower() == "true":
            logging.info("Running job application task")
            jobs_applied = apply_for_jobs()
            logging.info(f"Job application task completed. Applied to {jobs_applied} jobs")
        else:
            logging.info("Job application is disabled in settings")
            
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds() / 60  # Duration in minutes
        logging.info(f"All tasks completed in {duration:.1f} minutes")
        
        # Calculate and log next run time
        interval_hours = int(os.getenv("INTERVAL_HOURS", "1"))  # Default to 1 hour
        if interval_hours > 0:
            next_run = datetime.now() + timedelta(hours=interval_hours)
            logging.info(f"Next scheduled run: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        
        logging.info("=== Task Execution Complete ===")
        
    except Exception as e:
        logging.error(f"Error executing scheduled tasks: {e}")

# Initialize logging
setup_logging()

# Get environment variables with defaults
INTERVAL_HOURS = int(os.getenv("INTERVAL_HOURS", "1"))  # Default to 1 hour

# Set up the schedule
if INTERVAL_HOURS > 0:
    schedule.every(INTERVAL_HOURS).hours.do(job)
    logging.info(f"Naukri automation bot started. Running every {INTERVAL_HOURS} hours")
else:
    logging.info("Running in single execution mode (no schedule)")

# Run the job immediately at startup
job()

# If in scheduled mode, keep running
if INTERVAL_HOURS > 0:
    while True:
        try:
            schedule.run_pending()
            time.sleep(60)  # Check every minute to reduce CPU usage
        except Exception as e:
            logging.error(f"Scheduler error: {e}")
            time.sleep(300)  # Wait 5 minutes on error before trying again
