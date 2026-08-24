from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive.file"
]

flow = InstalledAppFlow.from_client_secrets_file(
    "credentials.json",
    SCOPES
)

creds = flow.run_local_server(
    port=8080,
    open_browser=False
)

with open("token.json", "w") as token:
    token.write(creds.to_json())

print("AUTH BERHASIL - token.json dibuat")
