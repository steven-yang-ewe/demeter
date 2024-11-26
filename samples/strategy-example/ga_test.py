from google.oauth2 import service_account
from googleapiclient.discovery import build
import pandas as pd

if __name__ == "__main__":
    # Load the service account credentials
    SCOPES = ['https://www.googleapis.com/auth/analytics.readonly']
    SERVICE_ACCOUNT_FILE = '/Users/lunaspeed/Documents/ewe/ewe-remixdao-prod-c686f82f7852.json'

    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)

    # Initialize the Analytics Reporting API V4
    analytics = build('analyticsreporting', 'v4', credentials=credentials)

    # Replace with your view ID
    VIEW_ID = '447532399' # '452860129'

    # Create the request to get DAU data
    response = analytics.reports().batchGet(
        body={
            'reportRequests': [
                {
                    'viewId': VIEW_ID,
                    'dateRanges': [{'startDate': '7daysAgo', 'endDate': 'today'}],
                    'metrics': [{'expression': 'ga:activeUsers'}],
                    'dimensions': [{'name': 'ga:date'}]
                }
            ]
        }
    ).execute()

    # Process the response
    rows = response['reports'][0]['data']['rows']
    data = []
    for row in rows:
        date = row['dimensions'][0]
        active_users = row['metrics'][0]['values'][0]
        data.append({'date': date, 'active_users': active_users})

    # Convert to DataFrame for easier manipulation
    df = pd.DataFrame(data)
    print(df)
