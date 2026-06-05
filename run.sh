#!/bin/sh

# Assume environment variable for token is MERAKI_API_TOKEN.

# Test if we have a report stored.
if [ -f "./json/recent.json "]; then
    rm ./json/recent.json
fi
if [ -f "./report/recent.pdf"]; then
    rm ./report/recent.pdf
fi

# Run the crawler
python3 ./src/meraki_ap_crawler.py --api-key $MERAKI_API_TOKEN --all-orgs --output ./json/recent.json
# Make the report
python3 ./src/meraki_ap_report.py --input ./json/recent.json --output ./report/recent.pdf

