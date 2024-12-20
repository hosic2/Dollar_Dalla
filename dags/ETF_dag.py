from airflow import DAG
from airflow.decorators import task
from airflow.providers.postgres.hooks.postgres import PostgresHook
from datetime import datetime, timedelta

import yfinance as yf
import pandas as pd
import logging

def get_Redshift_connection(autocommit=True):
    hook = PostgresHook(postgres_conn_id='redshift_dev_db')
    conn = hook.get_conn()
    conn.autocommit = autocommit
    return conn.cursor()


def get_next_week_dates(execution_date_str):
    execution_date = datetime.strptime(execution_date_str, "%Y-%m-%d")

    start_date = execution_date + timedelta(days=1)
    end_date = execution_date + timedelta(days=6)
    
    return start_date, end_date


def fetch_ETF_data(sector, symbol, start_date, end_date):
    data = pd.DataFrame()   # Date, open, close, volume, sector
    
    ticker = yf.Ticker(symbol)
    history = ticker.history(start=start_date, end=end_date)

    data['open'] = round(history['Open'], 3)
    data['close'] = round(history['Close'], 3)
    data['volume'] = history['Volume']
    data['sector'] = sector

    data.reset_index(inplace=True)
    data.rename(columns={'index': 'Date'}, inplace=True)
    data['Date'] = data['Date'].dt.date
    
    return data


@task
def get_historical_data(execution_date_str, symbols):
    full_data = pd.DataFrame()
    
    for sec, sym in symbols.items():
        st, en = get_next_week_dates(execution_date_str)
        last_week_data = fetch_ETF_data(sec, sym, st, en)

        last_week_data["Date"] = pd.to_datetime(last_week_data["Date"])
        full_date_range = pd.date_range(start=st, end=en+timedelta(days=1), freq="D")
        last_week_data = last_week_data.set_index("Date").reindex(full_date_range).reset_index()
        last_week_data.rename(columns={"index": "Date"}, inplace=True)
        last_week_data["sector"] = last_week_data["sector"].fillna(sec)
        full_data = pd.concat([full_data, last_week_data])
    
    return full_data


def _create_table(cur, schema, table):
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {schema}.{table} (
            date date,
            sector varchar(20) NOT NULL,
            open_value float,
            close_value float,
            volume bigint
        );""")


@task
def load(schema, table, records):
    logging.info("load started")
    cur = get_Redshift_connection()
    try:
        cur.execute("BEGIN;")
        _create_table(cur, schema, table)

        for _, values in records.iterrows():
            if pd.isna(values[1]):
                sql = f"""
                    INSERT INTO {schema}.{table}
                    VALUES ('{datetime.date(values[0])}',
                            '{values[4]}',
                            null,
                            null,
                            null);"""
            else:
                sql = f"""
                    INSERT INTO {schema}.{table} 
                    VALUES ('{datetime.date(values[0])}',
                            '{values[4]}',
                            {values[1]},
                            {values[2]},
                            {values[3]});"""
            print(sql)
            cur.execute(sql)

        cur.execute("COMMIT;")

    except Exception as error:
        print(error)
        cur.execute("ROLLBACK;")
        raise

    logging.info("load complete")


with DAG(
    dag_id = 'ETF_dag',
    start_date = datetime(2023, 1, 1),
    catchup=True,
    tags=['ETF'],
    schedule = '0 15 * * 0',
    max_active_runs=1,
) as dag:
    symbols = {
    "산업" : "XLI",
    "헬스케어" : "XLV",
    "소비재" : "XLY",
    "소비재 필수품" : "XLP",
    "기술" : "XLK",
    "금융" : "XLF",
    "에너지" : "XLE",
    "통신" : "XLC",
    "유틸리티" : "XLU",
    "부동산" : "XLRE",
    "재료" : "XLB",
    "생명공학" : "XBI",
    "반도체" : "SMH",
    "금속/광업" : "XME",
    "농업" : "MOO",
    "수송" : "IYT" 
}
    execution_date_str = '{{ ds }}'
    results = get_historical_data(execution_date_str, symbols)
    load("musk82155", "ETF", results)