# 🚀 Naukri Bot: Automate Your Job Hunt Like a Pro! 💼

**Naukri Bot** is your all-in-one job-hunting sidekick. It automatically logs into your Naukri.com account, updates your profile, applies to relevant jobs, and even rotates headlines — all without you lifting a finger. Just set it and forget it ⏰💻

Built with Selenium, Python, and Docker, it smartly mimics human behavior to avoid detection, and even runs on a custom interval you define via `.env`.

---

## ✨ Features

- **Auto-Triggered Process**: Automatically runs itself based on the interval (in hours) that *you* define in `.env`.
- **Screenshot Cleanup**: Cleans up old screenshots before every new run to save space and avoid clutter.
- **Headline Update**: Rotates your profile headline from a pre-defined list to keep your profile fresh and engaging.
- **Resume Update**: Re-uploads your resume to keep it at the top of recruiter lists.
- **Job Applications**: Applies to jobs based on title, location, and experience with smart filters:
  - **Freshness**: Only jobs posted in the last 1 day.
  - **Relevance**: Prioritizes the most matching roles.
  - **Direct Apply Only**: Skips jobs that redirect to external sites.
- **Early Access Roles**: Shares interest in premium early access roles (for Naukri Premium users).
- **Detection Prevention**: Selenium config is tuned to bypass bot detection techniques.
- **Customizable Settings**: Easily tweak job titles, locations, experience, and max applications in `.env`.
- **Docker Support**: Fully containerized — works on any machine with Docker.
- **Improved Login System**: Multiple fallback login flows for more reliability.
- **Detailed Logging**: Saves logs and screenshots of each step (especially useful for debugging).

---

## 🛠️ Prerequisites

Make sure you have the following installed:

- [Docker](https://www.docker.com/) *(Recommended)*
- Python 3.8+
- Google Chrome
- ChromeDriver (auto-managed via `webdriver-manager`)

---

## 📂 Setup Instructions

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/your-username/naukri-bot.git
   cd naukri-bot
   ```

2. **Configure Your Environment**:
   - Create and update your `.env`:
     ```env
     NAUKRI_EMAIL=your_email
     NAUKRI_PASSWORD=your_password
     INTERVAL_HOURS=1
     RUN_SUMMARY_ROTATION=true
     RUN_JOB_APPLICATIONS=true
     EARLY_ACCESS_ROLES=true
     JOB_TITLES=DevOps Engineer, Site Reliability Engineer
     JOB_LOCATIONS=Bengaluru
     JOB_EXPERIENCE=2
     MAX_APPLICATIONS=3
     EARLY_ACCESS_ROLES_LIMIT=15
     ```

   - Edit `headlines.json`:
     ```json
     [
       { "headline": "Your Custom Headline 1" },
       { "headline": "Your Custom Headline 2" }
     ]
     ```

   - Place your updated resume as `resume.pdf` in the root directory.

3. **Run the Bot**:
   - With Docker:
     ```bash
     docker-compose up --build
     ```
   - Locally with Python:
     ```bash
     pip install -r requirements.txt
     python3 apply_jobs.py
     ```

   - ✅ The bot will automatically run every `INTERVAL_HOURS` (e.g. every hour) as configured.

---

## ⚙️ File Structure

```
naukri-bot/
├── apply_jobs.py         # Main automation script
├── rotate_headline.py    # Headline update logic
├── share_interest.py     # Handles early access interest
├── utils.py              # Helper functions (cleanup, login, filters)
├── headlines.json        # Your rotating headlines list
├── resume.pdf            # Your latest resume (local only)
├── .env                  # Config file with user preferences
├── Dockerfile            # Docker setup
├── docker-compose.yml    # Run with docker-compose
├── README.md             # This doc
├── debug/                # Stores logs and screenshots per run
```

---

## ⚠️ Limitations

- Jobs with "Apply on Company Site" are skipped by design.
- May need updates if Naukri's HTML structure changes.
- Selenium-based — avoid running too aggressively to reduce detection risk.
- If running on a physical machine have to disable sleep.

---

## 👥 Contributing

Pull up, devs! 🚀 Wanna improve it? Add new modules? Support other job sites? Go crazy:

1. Fork the repo 🍴  
2. Create a feature branch:
   ```bash
   git checkout -b feat/my-feature
   ```
3. Commit and push:
   ```bash
   git commit -m 'feat: added new module'
   git push origin feat/my-feature
   ```
4. Open a Pull Request 💌

Feel free to open issues for bugs, enhancements, or questions!

---

## 📜 License

MIT License © 2025 [Barkat Shaik]  
See the `LICENSE` file for full terms. You’re free to use, remix, and share — just give credit!

---

## 💬 Questions or Feedback?

- File an issue here on GitHub  
- Or reach out directly: `skbarkat444@gmail.com`

Let’s automate job hunting like it’s 2050 😎
