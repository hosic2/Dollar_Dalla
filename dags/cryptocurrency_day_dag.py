from airflow import DAG
from airflow.decorators import task
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.models import Variable

from datetime import datetime
from datetime import timedelta
import requests
import pandas as pd
import logging


def get_Redshift_connection(autocommit=True):
    hook = PostgresHook(postgres_conn_id='redshift_dev_db')
    conn = hook.get_conn()
    conn.autocommit = autocommit
    return conn.cursor()


def convert_to_unix_timestamp(date_string):
    # "YYYY-mm-dd" 형식의 날짜 문자열을 유닉스 타임스탬프로 변환 -> binance에서 유닉스 시간만 지원
    # 당일 데이터 적재를 위해 excution_date에 1일 추가 연산
    return int((datetime.strptime(date_string, '%Y-%m-%d') + timedelta(days=1)).timestamp() * 1000) 


def fetch_binance_data(name, symbol, date):
    url = f'https://api.binance.com/api/v3/klines'
    params = {
        'symbol': symbol,
        'interval': '1d',
        'startTime': convert_to_unix_timestamp(date),
        'endTime': convert_to_unix_timestamp(date)
    }
    
    response = requests.get(url, params=params)
    data = response.json()
    
    df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df[['timestamp', 'open', 'close', 'volume']]
    
    # 이름, 날짜, 시장 시작가, 종가, 거래량만 반환
    records = [
        [name, row['timestamp'].strftime("%Y-%m-%d"), row['open'], row['close'], row['volume']]
        for _, row in df.iterrows()
    ]
    
    return records


@task
def get_historical_prices(symbols, date):
    records = []
    for name, symbol in symbols.items():
        data = fetch_binance_data(name, symbol, date)
        records.extend(data)
    
    return records


def _create_table(cur, schema, table):
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {schema}.{table} (
            name varchar(20) NOT NULL,
            date date,
            open_value float,
            close_value float,
            volume bigint
        );
    """)


@task
def load(schema, table, records):
    logging.info("load started")
    cur = get_Redshift_connection()
    try:
        cur.execute("BEGIN;")
        # 원본 테이블이 없으면 생성
        _create_table(cur, schema, table)

        for r in records:
            sql = f"""
                    INSERT INTO {schema}.{table} (name, date, open_value, close_value, volume)
                    VALUES ('{r[0]}', '{r[1]}', ROUND({r[2]}, 2), ROUND({r[3]}, 2), {r[4]});
                    """
            print(sql)
            cur.execute(sql)

        cur.execute("COMMIT;")

    except Exception as error:
        print(error)
        cur.execute("ROLLBACK;")
        raise

    logging.info("load done")


with DAG(
    dag_id='cryptocurrency_day_dag55',
    start_date=datetime(2024, 11, 22),
    catchup=True,
    tags=['API'],
    schedule_interval='@daily',
    max_active_runs=1,
) as dag:
    # 가상화폐 심볼 정의
    symbols = {
        "비트코인": "BTCUSDT",
        "이더리움": "ETHUSDT",
        "리플": "XRPUSDT",
        "이오스": "EOSUSDT",
        "스텔라루멘": "XLMUSDT",
        "라이트코인": "LTCUSDT",
        "도지코인": "DOGEUSDT",
        "비트코인캐시": "BCHUSDT",
    }
    
    # 데이터를 가져오는 task
    results = get_historical_prices(symbols, "{{ ds }}")
    
    # Redshift에 데이터 로드하는 task
    load(Variable.get("redshift_schema_name"), "cryptocurrency_day", results)