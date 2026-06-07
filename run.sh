#!/bin/sh

# Assume environment variable for token is MERAKI_API_TOKEN.
#MERAKI_API_TOKEN=YOUR TOKEN HERE

# Assume environment variable for the outbound email server is in MERAKI_HEALTH_REPORT_MAIL_SERVER
#MERAKI_HEALTH_REPORT_MAIL_SERVER=YOU.EMAIL.SERVER.NET

# Assume environment variable for the from: email address is in MERAKI_HEALTH_REPORT_FROM
#MERAKI_HEALTH_REPORT_FROM=meraki_ap_health_reports@localhost

# Assume environment variable for the destination email address is in MERAKI_HEALTH_REPORT_TO
#MERAKI_HEALTH_REPORT_TO=admin@localhost

# Test if we have a report stored.
if [ -f "./json/recent.json" ]; then
    rm ./json/recent.json
fi
if [ -f "./report/recent.pdf" ]; then
    rm ./report/recent.pdf
fi

# Enter venv
source ./venv/bin/activate

# Do work
python3 ./src/meraki_ap_crawler.py \
    --api-key $MERAKI_API_TOKEN \
    --all-orgs \
    --output ./json/recent.json
python3 ./src/meraki_ap_report.py \
    --input ./json/recent.json \
    --output ./report/recent.pdf
python ./src/send_email.py \
  --server $MERAKI_HEALTH_REPORT_MAIL_SERVER \
  --port 25 \
  --from $MERAKI_HEALTH_REPORT_FROM \
  --to $MERAKI_HEALTH_REPORT_TO \
  --title "Meraki AP Report" \
  --message "See attached." \
  --attachment ./report/recent.pdf

# Exit venv
deactivate