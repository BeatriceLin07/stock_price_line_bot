from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from openai import OpenAI
from openai import RateLimitError, APIError
import requests
from datetime import datetime
from pymongo import MongoClient
import os


app = Flask(__name__)


ALPHA_VANTAGE_API_KEY = os.getenv('ALPHA_VANTAGE_API_KEY')
ALPHA_VANTAGE_BASE_URL = os.getenv('ALPHA_VANTAGE_BASE_URL')
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
MONGO_CONNECTION_STRING = os.getenv('MONGO_CONNECTION_STRING')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# print("LINE_CHANNEL_ACCESS_TOKEN:", LINE_CHANNEL_ACCESS_TOKEN)
# print("LINE_CHANNEL_SECRET:", LINE_CHANNEL_SECRET)
# print("ALPHA_VANTAGE_API_KEY:", ALPHA_VANTAGE_API_KEY)
# print("ALPHA_VANTAGE_BASE_URL:", ALPHA_VANTAGE_BASE_URL)
# print("OPENAI_API_KEY:", OPENAI_API_KEY)
# print("MONGO_CONNECTION_STRING:", MONGO_CONNECTION_STRING)


# Initialize Configuration and WebhookHandler
configuration = Configuration(
    access_token = LINE_CHANNEL_ACCESS_TOKEN,
)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# API configurations
client = OpenAI(api_key = OPENAI_API_KEY)

# MongoDB configuration
mongo_client = MongoClient(MONGO_CONNECTION_STRING)
db = mongo_client['stockbot_db']
user_queries = db['user_queries']


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

def get_ticker_from_openai(company_name):
    prompt = f"""
    Given the company name or description: "{company_name}", 
    provide only the most likely stock ticker symbol. 
    Respond with just the ticker symbol, nothing else.
    """

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that provides stock ticker symbols."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )
        return response.choices[0].message.content.strip()
    except (RateLimitError, APIError) as e:
        print(f"OpenAI API error: {str(e)}. Using fallback method.")
        return fallback_get_ticker(company_name)

def fallback_get_ticker(company_name):
    return company_name.split()[0].upper()

def get_stock_price(ticker):
    url = f"{ALPHA_VANTAGE_BASE_URL}?function=GLOBAL_QUOTE&symbol={ticker}&apikey={ALPHA_VANTAGE_API_KEY}"
    response = requests.get(url)
    data = response.json()

    if "Global Quote" in data and "05. price" in data["Global Quote"]:
        return float(data["Global Quote"]["05. price"])
    else:
        return None

def get_stock_info(company_name, user_id):
    # Check if we've seen this company before
    existing_query = user_queries.find_one({"user_id": user_id, "company_name": company_name})
    if existing_query:
        ticker = existing_query['ticker']
    else:
        ticker = get_ticker_from_openai(company_name)
        # Store the new query
        user_queries.insert_one({
            "user_id": user_id,
            "company_name": company_name,
            "ticker": ticker,
            "timestamp": datetime.now()
        })

    price = get_stock_price(ticker)

    if price is not None:
        return f"Ticker: {ticker}\nCurrent Stock Price: ${price:.2f}"
    else:
        return f"Unable to retrieve current stock price for {ticker}."

def get_user_history(user_id):
    user_history = user_queries.find({"user_id": user_id}).sort("timestamp", -1).limit(5)
    history_text = "Your recent queries:\n"
    for query in user_history:
        ticker = query['ticker']
        price = get_stock_price(ticker)
        if price is not None:
            history_text += f"- {query['company_name']} ({ticker}): ${price:.2f}\n"
        else:
            history_text += f"- {query['company_name']} ({ticker}): Price not available\n"
    return history_text

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text.lower()

    if user_message == "history":
        reply_text = get_user_history(user_id)
    else:
        stock_info = get_stock_info(user_message, user_id)
        reply_text = stock_info + "\n\nType 'history' to see your recent queries."

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        reply_request = ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=reply_text)]
        )
        line_bot_api.reply_message_with_http_info(reply_request)

if __name__ == "__main__":
    app.run(debug=True, port=4999)
