%%writefile strategy.py
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import time

# Define the main function to run the strategy check
def run_strategy_check():
    """Fetches data, calculates indicators, checks conditions, and sends alerts."""
    # Fetch the latest 1-hour data for QQQ (e.g., last few days to ensure enough data for indicators)
    end_date = pd.Timestamp.now(tz='UTC')
    # Fetch enough historical data for indicator calculation (e.g., 30 days)
    start_date = end_date - pd.Timedelta(days=30)

    try:
        latest_qqq_data = yf.download("QQQ", start=start_date, end=end_date, interval="1h")
    except Exception as e:
        print(f"Error fetching data: {e}")
        send_telegram_message(f"Error fetching QQQ data: {e}")
        return

    # Ensure the fetched data is not empty and has the expected structure
    if latest_qqq_data.empty:
        print("Could not fetch latest data.")
        send_telegram_message("Could not fetch latest QQQ data.")
        return

    # Flatten multi-level columns if they exist, preserving the original column names
    if isinstance(latest_qqq_data.columns, pd.MultiIndex):
        latest_qqq_data.columns = ['_'.join(col).strip() if col[1] else col[0].strip() for col in latest_qqq_data.columns.values]


    # 1. Define a function to calculate the Relative Strength Index (RSI)
    def calculate_rsi(data, period=14):
        delta = data.diff()
        gain = delta.mask(delta < 0, 0)
        loss = delta.mask(delta > 0, 0).abs()
        avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    # 2. Calculate the Volume Weighted Average Price (VWAP)
    # Use the corrected flattened column names
    try:
        latest_qqq_data['Typical_Price'] = (latest_qqq_data['High_QQQ'] + latest_qqq_data['Low_QQQ'] + latest_qqq_data['Close_QQQ']) / 3
        latest_qqq_data['Cumulative_TP_Volume'] = (latest_qqq_data['Typical_Price'] * latest_qqq_data['Volume_QQQ']).cumsum()
        latest_qqq_data['Cumulative_Volume'] = latest_qqq_data['Volume_QQQ'].cumsum()
        latest_qqq_data['VWAP'] = latest_qqq_data['Cumulative_TP_Volume'] / latest_qqq_data['Cumulative_Volume']
    except KeyError as e:
        print(f"Error accessing expected columns after flattening: {e}")
        print("Available columns:", latest_qqq_data.columns.tolist())
        send_telegram_message(f"Error accessing columns in QQQ data: {e}")
        return


    # 3. Calculate the Exponential Moving Average (EMA) for QQQ close prices
    ema_period = 15 # Changed EMA period as per instructions
    latest_qqq_data['EMA'] = latest_qqq_data['Close_QQQ'].ewm(span=ema_period, adjust=False).mean()

    # 4. Add the calculated RSI as a new column.
    latest_qqq_data['RSI'] = calculate_rsi(latest_qqq_data['Close_QQQ'])

    # Drop the intermediate columns used for VWAP calculation
    latest_qqq_data = latest_qqq_data.drop(columns=['Typical_Price', 'Cumulative_TP_Volume', 'Cumulative_Volume'])

    # Get the latest data point
    if latest_qqq_data.empty:
        print("No data points after indicator calculation.")
        return

    latest_row = latest_qqq_data.iloc[-1]

    # Define the updated check conditions based on the new thresholds
    def check_entry_condition_latest(data_row):
        """Checks if the entry conditions are met for the latest data row."""
        rsi_threshold = 45 # Changed RSI threshold as per instructions

        # Access columns using the corrected flattened names
        try:
            rsi_condition = data_row['RSI'] > rsi_threshold
            ema_condition = data_row['Close_QQQ'] > data_row['EMA']
            vwap_condition = data_row['Close_QQQ'] > data_row['VWAP']
            return rsi_condition and ema_condition and vwap_condition
        except KeyError as e:
            print(f"Error accessing indicator columns in entry check: {e}")
            return False

    def check_exit_condition_latest(data_row, buy_price, take_profit_percentage=2.5, stop_loss_percentage=0.5, trailing_stop_percentage=1.0):
        """Checks if the exit conditions are met for a given row of data, including TP, SL, and TS."""

        if buy_price is None:
            return False, None # Cannot check exit conditions if not in a position

        current_price = data_row['Close_QQQ']

        # Take Profit condition
        take_profit_price = buy_price * (1 + take_profit_percentage / 100)
        take_profit_hit = current_price >= take_profit_price
        if take_profit_hit:
            return True, "Take Profit"

        # Stop Loss condition
        stop_loss_price = buy_price * (1 - stop_loss_percentage / 100)
        stop_loss_hit = current_price <= stop_loss_price
        if stop_loss_hit:
            return True, "Stop Loss"

        # Trailing Stop condition (simplified for stateless hourly check)
        # As noted before, a true trailing stop requires state management (highest price since entry).
        # For this stateless script, we omit the true trailing stop logic.
        # If you were to implement this in a stateful application, you would track the highest price
        # reached since the position was opened and check if the current price has dropped by
        # trailing_stop_percentage from that peak.

        # If none of the exit conditions are met
        return False, None

    # Function to send Telegram message
    def send_telegram_message(message):
        """Sends a message to a Telegram bot."""
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        chat_id = os.getenv('TELEGRAM_CHAT_ID')

        if not bot_token or not chat_id:
            print("Telegram bot token or chat ID not set in environment variables.")
            return

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': message
        }

        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()  # Raise an HTTPError for bad responses (4xx or 5xx)
            print("Telegram message sent successfully.")
        except requests.exceptions.RequestException as e:
            print(f"Error sending Telegram message: {e}")

    # In a real-time stateless script like this for a scheduler,
    # we cannot reliably track 'in_position' and 'buy_price' across runs.
    # Therefore, we can only check for entry signals and send alerts.
    # Exit signals based on TP/SL/TS require state management.
    # A more sophisticated system would need a database or state file to track open positions.

    # Check entry condition for the latest data point
    if check_entry_condition_latest(latest_row):
        entry_message = (
            f"Entry signal triggered for QQQ at {latest_row.name.strftime('%Y-%m-%d %H:%M:%S')}:\n"
            f"Close: {latest_row['Close_QQQ']:.2f}, RSI: {latest_row['RSI']:.2f}, "
            f"EMA(15): {latest_row['EMA']:.2f}, VWAP: {latest_row['VWAP']:.2f}"
        )
        print(entry_message)
        send_telegram_message(entry_message)
    else:
        print(f"No entry signal at {latest_row.name.strftime('%Y-%m-%d %H:%M:%S')}.")

    # Note on Exit Conditions in Stateless Script:
    # The check_exit_condition_latest function is defined, but it requires 'buy_price'
    # which is state that this stateless script doesn't maintain across runs.
    # To implement exit condition checks and alerts, you would need to:
    # 1. Track if you are currently in a position.
    # 2. Store the buy price when an entry signal was acted upon.
    # 3. On each hourly run, if in a position, fetch the latest data,
    #    calculate indicators, and call check_exit_condition_latest with the stored buy price.
    # 4. If an exit is triggered, send an alert and update the state (e.g., mark position as closed).
    # This requires a stateful application or using a database/file to persist state.
    # The current script is designed as a simple hourly checker for *potential* signals.

# Main execution block
if __name__ == "__main__":
    print("Running QQQ momentum scalping strategy check...")
    run_strategy_check()
    print("Strategy check finished.")
