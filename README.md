# HireMeMaybe: Automate Your Job Hunt Like a Pro

**HireMeMaybe (Naukri Bot)** is your all-in-one job-hunting sidekick. It automatically logs into your Naukri.com account, updates your profile, applies to relevant jobs, and even rotates headlines -- all without you lifting a finger. Just set it and forget it.

Built with Selenium, Python, and Docker, it smartly mimics human behavior to avoid detection, and runs on a custom interval you define via `.env`.

---

## Features

- **Auto-Triggered Process**: Runs on a configurable interval (in hours) defined in `.env`.
- **Headline Rotation**: Rotates your profile headline from a pre-defined list to keep your profile fresh.
- **Resume Upload**: Re-uploads your resume to keep it at the top of recruiter lists.
- **Smart Job Applications**: Applies to jobs based on title, location, and experience with:
  - **Relevance Check**: Verifies job title and JD match your configured roles before applying.
  - **Freshness Filter**: Only jobs posted in the last 1 day.
  - **Direct Apply Only**: Skips jobs that redirect to external company sites.
  - **Application Deduplication**: Tracks applied jobs in `applied_jobs.txt` to never re-apply.
  - **Smart Pagination**: Navigates multiple result pages (up to 3) to find enough relevant jobs.
  - **Human-like Delays**: Randomized delays throughout to avoid detection.
- **Questionnaire Auto-Answer**: Automatically answers recruiter screening questions using:
  - **Config-based answers** for common questions (CTC, notice period, relocation, etc.) via `screening_answers.json`.
  - **Local LLM fallback** (Ollama) for open-ended or unexpected questions using your resume as context.
  - **Self-learning log** (`qa_log.json`) that records every Q&A for future config promotion.
- **Fallback Searches**: Retries with different job title/location combos if target applications aren't reached.
- **Early Access Roles**: Shares interest in premium early access roles (for Naukri Premium users).
- **Detection Prevention**: Selenium config tuned to bypass bot detection.
- **Containerised (Docker)**: Fully containerized -- works on any machine with Docker.
- **Detailed Logging**: Saves UTF-8 logs and screenshots of each step for debugging.

---

## Prerequisites

- [Docker](https://www.docker.com/) *(Recommended)* for easy deployment
- **For running locally** (optional):
  - Python 3.8+
  - Google Chrome
  - ChromeDriver (auto-managed via `webdriver-manager`)
  - [Ollama](https://ollama.com/) *(optional, for questionnaire LLM fallback)*

---

## Setup Instructions

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/Barkat444/HireMeMaybe.git
   cd HireMeMaybe
   ```

2. **Configure Your Environment**:

   Update `.env` with your credentials and preferences:
   ```env
   # Credentials
   NAUKRI_EMAIL=your_email
   NAUKRI_PASSWORD=your_password

   # General Settings
   INTERVAL_HOURS=1
   RUN_SUMMARY_ROTATION=true
   RUN_JOB_APPLICATIONS=true
   EARLY_ACCESS_ROLES=true

   # Job Application Settings
   JOB_TITLES=DevOps Engineer, Site Reliability Engineer
   JOB_LOCATIONS=Bengaluru
   JOB_EXPERIENCE=2
   MAX_APPLICATIONS=3
   EARLY_ACCESS_ROLES_LIMIT=15

   # Questionnaire Auto-Answer
   ANSWER_QUESTIONNAIRE=true
   OLLAMA_URL=http://localhost:11434
   OLLAMA_MODEL=llama3.1:8b
   ```

   Edit `headlines.json` with your rotating headlines:
   ```json
   [
     { "headline": "Your Custom Headline 1" },
     { "headline": "Your Custom Headline 2" }
   ]
   ```

   Place your resume as a `.pdf` in the project root (e.g., `HireMeMaybe/your_resume.pdf`).

3. **Configure Screening Answers** *(for questionnaire auto-answer)*:

   Edit the `profile` section in `screening_answers.json` with your actual details:
   ```json
   {
     "profile": {
       "current_ctc_lpa": "8",
       "expected_ctc_lpa": "12",
       "notice_period": "30 days",
       "current_location": "Bengaluru",
       "willing_to_relocate": "Yes",
       ...
     }
   }
   ```

   The file comes pre-loaded with 37 common Naukri question patterns. Any question not matched will be answered by the local LLM using your resume context.

4. **Setup Ollama** *(optional, for LLM fallback)*:
   ```bash
   ollama pull llama3.1:8b
   ```
   Make sure Ollama is running before starting the bot.

5. **Run the Bot**:

   With Docker:
   ```bash
   docker-compose up --build
   ```

   Or locally with Python:
   ```bash
   pip install -r requirements.txt
   python main.py
   ```

   The bot will automatically run every `INTERVAL_HOURS` as configured.

---

## File Structure

```
HireMeMaybe/
├── main.py                  # Entry point, scheduler
├── apply_jobs.py            # Job search, relevance check, apply logic
├── rotate_headline.py       # Headline rotation + resume upload
├── share_interest.py        # Early access roles (share interest)
├── questionnaire_handler.py # Screening questionnaire auto-answer
├── utils.py                 # Helper functions (driver init, login, screenshots)
├── screening_answers.json   # Pre-configured answers for common questions
├── headlines.json           # Rotating headlines list
├── .env                     # Config file with user preferences
├── requirements.txt         # Python dependencies
├── Dockerfile               # Docker setup
├── docker-compose.yml       # Run with docker-compose
├── applied_jobs.txt         # Auto-generated: tracks applied job URLs (dedup)
├── qa_log.json              # Auto-generated: logs all Q&A from questionnaires
├── debug/                   # Stores logs and screenshots per run
```

---

## How the Questionnaire Auto-Answer Works

```
Click Apply
    |
    v
Detect Sidebar/Questionnaire
    |
    +--> No sidebar --> Check success message
    |
    +--> Sidebar found --> Extract questions + input types
              |
              v
         For each question:
              |
              +--> Match in screening_answers.json? --> Fill from config
              |
              +--> No match? --> Ask Ollama (with resume context) --> Fill answer
              |
              +--> Log Q&A to qa_log.json
              |
              v
         Click Submit --> Check success message
```

Review `qa_log.json` periodically and promote good Ollama answers into `screening_answers.json` for deterministic handling in future runs.

---

## Limitations

- Jobs with "Apply on Company Site" are skipped by design.
- May need selector updates if Naukri's HTML structure changes.
- Selenium-based -- avoid running too aggressively to reduce detection risk.
- Questionnaire handling depends on Naukri's DOM structure; novel layouts may need selector additions.
- If running on a physical machine, disable sleep mode.

---

## Contributing

1. Fork the repo
2. Create a feature branch:
   ```bash
   git checkout -b feat/my-feature
   ```
3. Commit and push:
   ```bash
   git commit -m "feat: added new module"
   git push origin feat/my-feature
   ```
4. Open a Pull Request

Feel free to open issues for bugs, enhancements, or questions.

---

## License

MIT License (c) 2025 [Barkat Shaik]
See the `LICENSE` file for full terms.

---

## Questions or Feedback?

- File an issue on GitHub
- Or reach out directly: `skbarkat444@gmail.com`
