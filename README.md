# Researcher-Productivity-Tool

Nowadays, researchers in top universities are required to complete their research profiles. Such a move is necessary and important to increase the publicity of research output and potentially attract more public attention. However, across universities, webpages are arranged differently, creating difficulties for comparisons in research productivity across universities. This potentially affects public awareness of research quality. The goal of the project is to produce a tool that can collect publication information from Australian G8 universities, say, in the "Accounting/Finance" field and, based on a ranking list, to classify these publications, summarize all researchers' productivity, and make comparisons across universities. Minimum goal: Input - information from various webpages: 

1) corresponding webpages of individual researchers in Accounting/Finance in all G8 universities; 
2) ABDC journal ranking information: https://abdc.edu.au/abdc-journal-quality-list/ Output - a webpage to display individuals' numbers of publications in A*-, A-, B- and other categories, and it allows users to download the information as an Excel spreadsheet. On the webpage, for each individual researcher, there are hyperlinks to display a table of journal names, number of publications, year, keywords, etc. Medium-level goal: Output - dashboard for individual researchers' output based on various rankings. Apart from the ABDC list (in the minimum goal), use other journal rankings such as H-index, Journal Impact Factor, Clarivate. Beyond: Add more visualization tools that display the productivity of individual researchers; Provide summaries at the university level; Expand the field into other subjects such as Engineering, Computer Science, etc.

## Group of Eight Universities

1. The University of Melbourne
    - https://fbe.unimelb.edu.au/about/academic-staff?queries_tags_query=4895953 (Finance)
    - https://fbe.unimelb.edu.au/about/academic-staff?queries_tags_query=4895951 (Accounting)
2. The Australian National University
    - https://researchportalplus.anu.edu.au/en/organisations/anu-college-of-business-and-economics/persons/ (business & economics)
    - https://researchportalplus.anu.edu.au/en/organisations/research-school-of-finance-actuarial-studies-statistics/persons/
4. The University of Sydney
    - https://www.sydney.edu.au/research/our-research/find-a-researcher.html?+facultyCode=5000053050F0000&+schoolCode=5000053050F0000F2050&+departmentCode=5000053050F0000F2050F0200&Academic=true (Buinsness School/ Accounting)
    - https://www.sydney.edu.au/research/our-research/find-a-researcher.html?+facultyCode=5000053050F0000&+schoolCode=5000053050F0000F2050&+departmentCode=5000053050F0000F2050F0300&Academic=true (Buinsness School/ Finance)
6. The University of Queensland
    - https://business.uq.edu.au/research/research-areas/accounting (Accounting)
    - https://business.uq.edu.au/research/research-areas/finance (Finance)
7. The University of New South Wales
    - https://www.unsw.edu.au/business/our-people#search=&filters=f.School%257CstaffSchool%3ASchool%2Bof%2BAccounting%252C%2BAuditing%2Band%2BTaxation&sort=metastaffLastName&startRank=1&numRanks=12 (Accounting)
    - https://www.unsw.edu.au/business/our-people#search=&filters=f.School%257CstaffSchool%3ASchool%2Bof%2BBanking%2Band%2BFinance&sort=metastaffLastName&startRank=1&numRanks=12 (Finance)
9. Monash University
    - https://research.monash.edu/en/organisations/department-of-accounting/persons/ (Accounting)
    - https://research.monash.edu/en/organisations/banking-finance/persons/ (Finance)
    - https://research.monash.edu/en/organisations/centre-for-quantitative-finance-and-investment-strategies/persons/ (Finance)
10. The University of Western Australia
    - https://www.uwa.edu.au/schools/business/accounting-and-finance
11. The University of Adelaide
    - https://digital.library.adelaide.edu.au/browse/author?scope=9952f540-778a-45dd-a309-6604e8753669 (Business)
    - https://business.adelaide.edu.au/research/accounting#lead-researchers (lead accounting reasearchers)
    - https://business.adelaide.edu.au/research/finance-and-business-analytics (finance and business analytics staff members)

## Deployment Documentation

### 1. Prerequisites
- Ubuntu 22.04 EC2 instance with inbound SSH (22) and HTTP (80) open.
- An SSH private key (`r_tool.pem`) that allows login as `ubuntu`.
- Local machine with `ssh`, `rsync`, and Python 3.10+ installed.

### 2. Sync Project to EC2
```bash
# On local machine
chmod 400 r_tool.pem
ssh -i r_tool.pem ubuntu@3.25.59.145
rsync -avz -e "ssh -i r_tool.pem" --delete Project/ ubuntu@3.25.59.145:~/deploy
```

### 3. Install Runtime Dependencies (on EC2)
```bash
sudo apt update
sudo apt install -y python3-venv python3-pip
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install ./google-chrome-stable_current_amd64.deb -y
sudo apt install -y chromium-chromedriver
```

### 4. Create Virtual Environment & Install Packages
```bash
cd ~/deploy
python3 -m venv deployment-venv
source deployment-venv/bin/activate
pip install -r requirements.txt
```

### 5. Run Application Manually (for smoke testing)
```bash
nohup xvfb-run -a uvicorn app.main:app --host 127.0.0.1 --port 8000 &
```
- Check logs in `nohup.out`.
- To stop, list processes with `ps aux | grep uvicorn` and `kill <PID>`.

### 6. Configure Apache Reverse Proxy
```bash
sudo apt install -y apache2 libapache2-mod-proxy-uwsgi
sudo a2enmod proxy
sudo a2enmod proxy_http
```
Create `/etc/apache2/sites-available/fastapi.conf`:
```apache
<VirtualHost *:80>
    ServerName your-domain.com

    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/

    ErrorLog ${APACHE_LOG_DIR}/fastapi_error.log
    CustomLog ${APACHE_LOG_DIR}/fastapi_access.log combined
</VirtualHost>
```
Enable site and restart Apache:
```bash
sudo a2ensite fastapi.conf
sudo systemctl restart apache2
```

### 7. Create systemd Service for Uvicorn
Create `/etc/systemd/system/fastapi.service`:
```ini
[Unit]
Description=FastAPI app
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/deploy
ExecStart=/home/ubuntu/deployment-venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```
Enable service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable fastapi
sudo systemctl start fastapi
```

### 8. Maintenance Commands
- Check service status: `sudo systemctl status fastapi`
- Restart app after deploy: `sudo systemctl restart fastapi`
- Tail Apache logs:
  ```bash
  sudo tail -f /var/log/apache2/fastapi_error.log
  sudo tail -f /var/log/apache2/fastapi_access.log
  ```
- Update code:
  ```bash
  rsync -avz -e "ssh -i r_tool.pem" --delete Project/ ubuntu@3.25.59.145:~/deploy
  ssh -i r_tool.pem ubuntu@3.25.59.145 "sudo systemctl restart fastapi"
  ```

