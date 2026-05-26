# RC_NIFTY-Roj_Ki_ROti
RC_NIFTY+Roj_Ki_ROti


Daily workflow
Every morning (before market opens):

cd ~/nifty-monitor
source venv/bin/activate
python generate_token.py

Then
python monitor.py



(venv) kumar@MyLabServer:~/nifty-monitor$ pwd
/home/kumar/nifty-monitor
(venv) kumar@MyLabServer:~/nifty-monitor$ ls -lrt
total 92
drwxrwxr-x 5 kumar kumar  4096 May  8 08:34 venv
-rw-rw-r-- 1 kumar kumar  1283 May  8 08:38 generate_token.py
-rw-rw-r-- 1 kumar kumar  4305 May  8 09:33 oi_snapshot.py
-rw-rw-r-- 1 kumar kumar 14327 May  8 09:42 dashboard.py
-rw-rw-r-- 1 kumar kumar 17933 May 14 07:52 staged_entry.py
-rw-rw-r-- 1 kumar kumar  8050 May 19 17:10 iron_condor_monitor.py
-rw-rw-r-- 1 kumar kumar  9403 May 19 17:22 iron_condor_setup.py
-rw-rw-r-- 1 kumar kumar  1371 May 21 08:51 positions.json
-rw-rw-r-- 1 kumar kumar 11081 May 21 08:52 monitor.py
drwxrwxr-x 2 kumar kumar  4096 May 25 07:54 logs
(venv) kumar@MyLabServer:~/nifty-monitor$



(venv) kumar@MyLabServer:~/nifty-monitor$ crontab -l

# EOD snapshot at 3:30 PM
# Hourly during market hours only (9AM to 3PM, Mon-Fri)
0 9,10,11,12,13,14 * * 1-5 cd /home/kumar/nifty-monitor && /home/kumar/nifty-monitor/venv/bin/python /home/kumar/nifty-monitor/monitor.py >> /home/kumar/nifty-monitor/logs/cron.log 2>&1

# EOD snapshot at 3:30 PM only
30 15 * * 1-5 cd /home/kumar/nifty-monitor && /home/kumar/nifty-monitor/venv/bin/python /home/kumar/nifty-monitor/monitor.py >> /home/kumar/nifty-monitor/logs/cron.log 2>&1
(venv) kumar@MyLabServer:~/nifty-monitor$



What the model ie you are suppose to do:
-------------------------------------------


1. Generate fresh access token
2. Check overnight news (Iran/crude)
3. Run iron_condor_setup.py at 9:20 AM
4. Decide: full IC / one side only / skip
5. Enter paper trade on Sensibull
6. Update positions.json
7. Confirm cron is running (iron_condor_monitor.py)
8. Share first hourly log with me


(venv) kumar@MyLabServer:~/nifty-monitor/logs$ ls -lrt
total 76
-rw-rw-r-- 1 kumar kumar 10855 May 21 15:30 ic_log_2026-05-21.log
-rw-rw-r-- 1 kumar kumar  9646 May 22 15:30 ic_log_2026-05-22.log
-rw-rw-r-- 1 kumar kumar 10013 May 25 15:30 ic_log_2026-05-25.log
-rw-rw-r-- 1 kumar kumar  1609 May 25 15:30 daily_tracker.csv
-rw-rw-r-- 1 kumar kumar 28724 May 25 15:30 cron.log
(venv) kumar@MyLabServer:~/nifty-monitor/logs$

