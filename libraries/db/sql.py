# importer
create_trades_table_sql = \
    ("CREATE TABLE IF NOT EXISTS trades ("
    "date DATE NOT NULL, "
    "symbol VARCHAR(4) NOT NULL, "
    "action VARCHAR(10) NOT NULL, "
    "num_shares INT, "
    "price_per_share DECIMAL(13, 2), "
    "total_price DECIMAL(13, 2) NOT NULL, "
    "account_type VARCHAR(100), "
    "PRIMARY KEY (date, symbol, action, total_price))")

create_dividends_table_sql = \
    ("CREATE TABLE IF NOT EXISTS dividends ("
    "date DATE NOT NULL, "
    "symbol VARCHAR(4) NOT NULL, "
    "dividend DECIMAL(13, 2) NOT NULL, "
    "account_type VARCHAR(100), "
    "PRIMARY KEY (date, symbol, dividend))")

create_splits_table_sql = \
    ("CREATE TABLE IF NOT EXISTS splits ("
    "record_date DATE NOT NULL, "
    "distribution_date DATE NOT NULL, "
    "symbol VARCHAR(4) NOT NULL, "
    "multiplier INT NOT NULL, "
    "PRIMARY KEY (record_date, distribution_date, symbol, multiplier))")

create_entities_table_sql = \
    ("CREATE TABLE IF NOT EXISTS entities ("
     "name VARCHAR(100) NOT NULL, "
     "symbol VARCHAR(4) NOT NULL, "
     "asset_type VARCHAR(100) NOT NULL, "
     "sector VARCHAR(100) NOT NULL, "
     "geography VARCHAR(100) NOT NULL, "
     "PRIMARY KEY (name, symbol, asset_type, sector, geography))")
    
create_acquisitions_table_sql = \
    ("CREATE TABLE IF NOT EXISTS acquisitions ("
    "date DATE NOT NULL, "
    "symbol VARCHAR(4) NOT NULL, "
    "acquirer VARCHAR(4) NOT NULL, "
    "conversion_ratio DECIMAL(13,5) NOT NULL, "
    "PRIMARY KEY (date, symbol, acquirer, conversion_ratio))")
    
insert_buysell_tx_sql = \
    ("INSERT IGNORE INTO trades"
     "(date, symbol, action, num_shares, price_per_share, total_price, account_type) "
     "VALUES ('{date}','{symbol}','{action}',{num_shares},"
             "{price_per_share},{total_price}, '{account_type}')")
    
insert_dividend_tx_sql = \
    ("INSERT IGNORE INTO dividends"
     "(date, symbol, dividend, account_type) "
     "VALUES ('{date}','{symbol}','{dividend}', '{account_type}')")

insert_entities_sql = \
    ("INSERT IGNORE INTO entities"
     "(name, symbol, asset_type, sector, geography) "
     "VALUES ('{name}','{symbol}','{asset_type}', '{sector}', '{geography}')")

delete_entities_single_sql = \
    ("DELETE FROM entities WHERE symbol = '{symbol}'")
    
insert_splits_sql = \
    ("INSERT IGNORE INTO splits"
     "(record_date, distribution_date, symbol, multiplier) "
     "VALUES ('{record_date}', '{distribution_date}', '{symbol}', '{multiplier}')")

insert_acquisitions_sql = \
    ("INSERT IGNORE INTO acquisitions"
     "(date, symbol, acquirer, conversion_ratio) "
     "VALUES ('{date}','{symbol}', '{acquirer}', '{conversion_ratio}')")

drop_splits_table_sql = "DROP TABLE IF EXISTS splits"
drop_entities_table_sql = "DROP TABLE IF EXISTS entities"
drop_summary_table_sql = "DROP TABLE IF EXISTS summary"

create_summary_table_sql = \
    ("CREATE TABLE IF NOT EXISTS summary ("
    "symbol VARCHAR(4) NOT NULL, "
    "name VARCHAR(100) NOT NULL, "
    "current_shares INT NOT NULL, "
    "cost_basis DECIMAL(13, 2) NOT NULL, "
    "first_purchase_date DATE NOT NULL, "
    "last_purchase_date DATE NOT NULL, "
    "total_dividend DECIMAL(13, 2), "
    "dividend_yield DECIMAL(3, 2), "
    "account_type VARCHAR(100), "
    "PRIMARY KEY (symbol, name, account_type))")

insert_summary_sql = \
    ("INSERT INTO summary"
     "(symbol, name, current_shares, cost_basis, "
     "first_purchase_date, last_purchase_date, total_dividend, dividend_yield, account_type) "
     "VALUES ('{symbol}','{name}','{current_shares}','{cost_basis}',"
             "'{first_purchase_date}','{last_purchase_date}',"
             "'{total_dividend}', '{dividend_yield}', '{account_type}') " 
     "ON DUPLICATE KEY UPDATE current_shares='{current_shares}'," 
     "cost_basis='{cost_basis}',first_purchase_date='{first_purchase_date}',"
     "last_purchase_date='{last_purchase_date}',total_dividend='{total_dividend}',"
     "dividend_yield='{dividend_yield}', account_type='{account_type}'")

stocks_with_sales_query = \
    ("SELECT t1.symbol, t1.bought, t2.sold, t1.bought-t2.sold as remaining FROM "
        "(SELECT symbol, sum(num_shares) as bought FROM trades WHERE action='buy' "
        "GROUP BY symbol order by symbol) t1, "
        "(SELECT symbol, sum(num_shares) as sold FROM trades WHERE action='sell' "
        "GROUP BY symbol order by symbol) t2 "
    "WHERE t1.symbol=t2.symbol AND t1.bought-t2.sold > 0")
stocks_with_sales_columns = ['Symbol', 'Bought', 'Sold', 'Remaining']

all_trades_query = "SELECT * FROM trades"
all_trades_columns = ['Date', 'Symbol', 'Action', 
                      'NumShares', 'PricePerShare','TotalPrice']

splits_query = "SELECT * FROM splits"
splits_columns = ['Record_Date', 'Distribution_Date', 'Symbol', 'Multiplier']

acquisitions_query = "SELECT * FROM acquisitions"
acquisitions_columns = ['Date', 'Symbol', 'Acquirer', 'Conversion_Ratio']

read_entities_table_query = "SELECT * FROM entities"
read_entities_table_columns = ['Name', 'Symbol', 'AssetType', 'Sector', 'Geography']

### Master Log Summary Method ###
master_log_buys_query = \
    "SELECT date, symbol, action, num_shares, price_per_share, account_type FROM trades WHERE action='buy'"
master_log_buys_columns = ['Date', 'Symbol', 'Action', 'Quantity', 'PricePerShare', 'AccountType']

master_log_sells_query = \
    "SELECT date, symbol, action, num_shares, account_type FROM trades WHERE action='sell'"
master_log_sells_columns = ['Date', 'Symbol', 'Action', 'Quantity', 'AccountType']

master_log_dividends_query = \
    "SELECT date, symbol, 'dividend' as 'action', dividend, account_type FROM dividends"
master_log_dividends_columns = ['Date', 'Symbol', 'Action', 'Dividend', 'AccountType']
                           
master_log_splits_query = \
    "SELECT distribution_date as 'date', symbol, 'split' as 'action', multiplier FROM splits"
master_log_splits_columns = ['Date', 'Symbol', 'Action', 'Multiplier']

master_log_acquisitions_query = \
    "SELECT date, symbol, acquirer, 'acquisition' as 'action', conversion_ratio FROM acquisitions"
master_log_acquisitions_columns = ['Date', 'Symbol', 'Acquirer', 'Action', 'Multiplier']

# Get asset full name and symbol from entities table
asset_name_query = "SELECT symbol,name FROM entities"
asset_name_columns = ['Symbol', 'Name']

read_summary_table_query = "SELECT * FROM summary"
read_summary_table_columns = ['Symbol', 'Name', 'Quantity', 'Cost Basis',
                              'First Purchase Date', 'Last Purchase Date', 
                              'Total Dividend', 'Dividend Yield', 'AccountType']
                                                          
# HistoryHelper - assets_history table
create_assets_history_table_sql = \
    ("CREATE TABLE IF NOT EXISTS assets_history ("
    "date DATE NOT NULL, "
    "symbol VARCHAR(4) NOT NULL, "
    "account_type VARCHAR(100) NOT NULL, "
    "quantity INT NOT NULL, "
    "cost_basis DECIMAL(13, 2) NOT NULL, "
    "closing_price DECIMAL(13, 2) NOT NULL, "
    "value DECIMAL(13, 2) NOT NULL, "
    "percent_return DECIMAL(13, 2) NOT NULL, "
    "PRIMARY KEY (date, symbol, account_type))")

insert_ignore_assets_history_sql = \
    ("INSERT IGNORE INTO assets_history"
     "(date, symbol, account_type, quantity, cost_basis, closing_price, value, percent_return) "
     "VALUES ('{date}','{symbol}', '{account_type}', '{quantity}', '{cost_basis}', '{closing_price}', '{value}', '{percent_return}')")

insert_update_assets_history_sql = \
    ("INSERT INTO assets_history"
     "(date, symbol, account_type, quantity, cost_basis, closing_price, value, percent_return) "
     "VALUES ('{date}','{symbol}', '{account_type}', '{quantity}', '{cost_basis}', '{closing_price}', '{value}', '{percent_return}') "
     "ON DUPLICATE KEY UPDATE "
     "date='{date}', symbol='{symbol}', account_type='{account_type}', quantity='{quantity}', "
     "cost_basis='{cost_basis}', closing_price='{closing_price}', value='{value}', percent_return='{percent_return}'")

read_assets_history_query = "SELECT * FROM assets_history"
read_assets_history_columns = ['Date', 'Symbol', 'AccountType', 'Quantity', 'CostBasis', 'ClosingPrice', 'Value', 'PercentReturn']

#HistoryHelper - portfolio_history table
create_portfolio_history_table_sql = \
    ("CREATE TABLE IF NOT EXISTS portfolio_history ("
    "date DATE NOT NULL, "
    "value DECIMAL(15, 2) NOT NULL, "
    "cost_basis DECIMAL(15, 2) NOT NULL, "
    "PRIMARY KEY (date))")

insert_ignore_portfolio_history_sql = \
    ("INSERT IGNORE INTO portfolio_history"
     "(date, value, cost_basis) VALUES ('{date}','{value}','{cost_basis}')")

insert_update_portfolio_history_sql = \
    ("INSERT INTO portfolio_history"
     "(date, value, cost_basis) VALUES ('{date}','{value}','{cost_basis}') "
     "ON DUPLICATE KEY UPDATE date='{date}', value='{value}', cost_basis='{cost_basis}'")

read_portfolio_history_query = "SELECT * FROM portfolio_history ORDER BY date"
read_portfolio_history_columns = ['Date', 'Value', 'CostBasis']

# HistoryHelper - assets_hypothetical_history table
create_assets_hypothetical_history_table_sql = \
    ("CREATE TABLE IF NOT EXISTS assets_hypothetical_history ("
    "date DATE NOT NULL, "
    "symbol VARCHAR(4) NOT NULL, "
    "quantity INT NOT NULL, "
    "closing_price DECIMAL(13, 2) NOT NULL, "
    "value DECIMAL(13, 2) NOT NULL, "
    "PRIMARY KEY (date, symbol))")
    
insert_ignore_assets_hypothetical_history_sql = \
    ("INSERT IGNORE INTO assets_hypothetical_history"
     "(date, symbol, quantity, closing_price, value) "
     "VALUES ('{date}','{symbol}','{quantity}','{closing_price}','{value}')")
    
insert_update_assets_hypothetical_history_sql = \
    ("INSERT INTO assets_hypothetical_history"
     "(date, symbol, quantity, closing_price, value) "
     "VALUES ('{date}','{symbol}', '{quantity}','{closing_price}','{value}') "
     "ON DUPLICATE KEY UPDATE "
     "date='{date}',symbol='{symbol}',quantity='{quantity}', "
     "closing_price='{closing_price}',value='{value}'")
    
read_assets_hypothetical_history_query = "SELECT * FROM assets_hypothetical_history"
read_assets_hypothetical_history_columns = ['Date', 'Symbol', 'Quantity', 'ClosingPrice', 'Value']

# SectorHistoryHelper - sectors_history table
create_sectors_history_table_sql = \
    ("CREATE TABLE IF NOT EXISTS sectors_history ("
    "date DATE NOT NULL, "
    "sector VARCHAR(40) NOT NULL, "
    "total_value DECIMAL(15, 2) NOT NULL, "
    "total_cost_basis DECIMAL(15, 2) NOT NULL, "
    "PRIMARY KEY (date, sector))")

insert_ignore_sectors_history_sql = \
    ("INSERT IGNORE INTO sectors_history"
     "(date, sector, total_value, total_cost_basis) "
     "VALUES ('{date}','{sector}','{total_value}','{total_cost_basis}')")

insert_update_sectors_history_sql = \
    ("INSERT INTO sectors_history"
     "(date, sector, total_value, total_cost_basis) "
     "VALUES ('{date}','{sector}','{total_value}','{total_cost_basis}') "
     "ON DUPLICATE KEY UPDATE "
     "date='{date}', sector='{sector}', "
     "total_value='{total_value}', total_cost_basis='{total_cost_basis}'")

read_sectors_history_query = "SELECT * FROM sectors_history"
read_sectors_history_columns = ['Date', 'Sector', 'TotalValue', 'TotalCostBasis']

# AssetTypeHistoryHelper - asset_types_history table
create_asset_types_history_table_sql = \
    ("CREATE TABLE IF NOT EXISTS asset_types_history ("
    "date DATE NOT NULL, "
    "asset_type VARCHAR(40) NOT NULL, "
    "total_value DECIMAL(15, 2) NOT NULL, "
    "total_cost_basis DECIMAL(15, 2) NOT NULL, "
    "PRIMARY KEY (date, asset_type))")

insert_ignore_asset_types_history_sql = \
    ("INSERT IGNORE INTO asset_types_history"
     "(date, asset_type, total_value, total_cost_basis) "
     "VALUES ('{date}','{asset_type}','{total_value}','{total_cost_basis}')")

insert_update_asset_types_history_sql = \
    ("INSERT INTO asset_types_history"
     "(date, asset_type, total_value, total_cost_basis) "
     "VALUES ('{date}','{asset_type}','{total_value}','{total_cost_basis}') "
     "ON DUPLICATE KEY UPDATE "
     "date='{date}', asset_type='{asset_type}', "
     "total_value='{total_value}', total_cost_basis='{total_cost_basis}'")

read_asset_types_history_query = "SELECT * FROM asset_types_history"
read_asset_types_history_columns = ['Date', 'AssetType', 'TotalValue', 'TotalCostBasis']

# AccountTypeHistoryHelper - account_types_history table
create_account_types_history_table_sql = \
    ("CREATE TABLE IF NOT EXISTS account_types_history ("
    "date DATE NOT NULL, "
    "account_type VARCHAR(40) NOT NULL, "
    "total_value DECIMAL(15, 2) NOT NULL, "
    "total_cost_basis DECIMAL(15, 2) NOT NULL, "
    "PRIMARY KEY (date, account_type))")

insert_ignore_account_types_history_sql = \
    ("INSERT IGNORE INTO account_types_history"
     "(date, account_type, total_value, total_cost_basis) "
     "VALUES ('{date}','{account_type}','{total_value}','{total_cost_basis}')")

insert_update_account_types_history_sql = \
    ("INSERT INTO account_types_history"
     "(date, account_type, total_value, total_cost_basis) "
     "VALUES ('{date}','{account_type}','{total_value}','{total_cost_basis}') "
     "ON DUPLICATE KEY UPDATE "
     "date='{date}', account_type='{account_type}', "
     "total_value='{total_value}', total_cost_basis='{total_cost_basis}'")

read_account_types_history_query = "SELECT * FROM account_types_history"
read_account_types_history_columns = ['Date', 'AccountType', 'TotalValue', 'TotalCostBasis']

# GeographyHistoryHelper - geography_history table
create_geography_history_table_sql = \
    ("CREATE TABLE IF NOT EXISTS geography_history ("
    "date DATE NOT NULL, "
    "geography VARCHAR(40) NOT NULL, "
    "total_value DECIMAL(15, 2) NOT NULL, "
    "total_cost_basis DECIMAL(15, 2) NOT NULL, "
    "PRIMARY KEY (date, geography))")

insert_ignore_geography_history_sql = \
    ("INSERT IGNORE INTO geography_history"
     "(date, geography, total_value, total_cost_basis) "
     "VALUES ('{date}','{geography}','{total_value}','{total_cost_basis}')")

insert_update_geography_history_sql = \
    ("INSERT INTO geography_history"
     "(date, geography, total_value, total_cost_basis) "
     "VALUES ('{date}','{geography}','{total_value}','{total_cost_basis}') "
     "ON DUPLICATE KEY UPDATE "
     "date='{date}', geography='{geography}', "
     "total_value='{total_value}', total_cost_basis='{total_cost_basis}'")

read_geography_history_query = "SELECT * FROM geography_history"
read_geography_history_columns = ['Date', 'Geography', 'TotalValue', 'TotalCostBasis']

# ============================================================
# Index definitions for performance optimization
# ============================================================
# MySQL doesn't support CREATE INDEX IF NOT EXISTS, so we use
# CREATE INDEX wrapped in a procedure or just catch duplicates.
# These are safe to run multiple times (duplicates are ignored).

# Transaction table indexes - speeds up symbol-based lookups in build_master_log()
create_index_trades_symbol_sql = \
    "CREATE INDEX idx_trades_symbol ON trades(symbol)"
create_index_trades_symbol_date_sql = \
    "CREATE INDEX idx_trades_symbol_date ON trades(symbol, date)"
create_index_dividends_symbol_sql = \
    "CREATE INDEX idx_dividends_symbol ON dividends(symbol)"
create_index_splits_symbol_sql = \
    "CREATE INDEX idx_splits_symbol ON splits(symbol)"

# History table indexes - speeds up symbol/dimension filtering
create_index_assets_history_symbol_sql = \
    "CREATE INDEX idx_assets_history_symbol ON assets_history(symbol)"
create_index_assets_hypo_history_symbol_sql = \
    "CREATE INDEX idx_assets_hypo_history_symbol ON assets_hypothetical_history(symbol)"
create_index_sectors_history_sector_sql = \
    "CREATE INDEX idx_sectors_history_sector ON sectors_history(sector)"
create_index_asset_types_history_type_sql = \
    "CREATE INDEX idx_asset_types_history_type ON asset_types_history(asset_type)"
create_index_account_types_history_type_sql = \
    "CREATE INDEX idx_account_types_history_type ON account_types_history(account_type)"
create_index_geography_history_geo_sql = \
    "CREATE INDEX idx_geography_history_geo ON geography_history(geography)"

# Entities table index - speeds up symbol lookups in add_asset_info()
create_index_entities_symbol_sql = \
    "CREATE INDEX idx_entities_symbol ON entities(symbol)"

# All transaction table indexes (for importer)
transaction_table_indexes = [
    create_index_trades_symbol_sql,
    create_index_trades_symbol_date_sql,
    create_index_dividends_symbol_sql,
    create_index_splits_symbol_sql,
    create_index_entities_symbol_sql,
]

# All history table indexes (for history handlers)
history_table_indexes = {
    'assets_history': [create_index_assets_history_symbol_sql],
    'assets_hypothetical_history': [create_index_assets_hypo_history_symbol_sql],
    'sectors_history': [create_index_sectors_history_sector_sql],
    'asset_types_history': [create_index_asset_types_history_type_sql],
    'account_types_history': [create_index_account_types_history_type_sql],
    'geography_history': [create_index_geography_history_geo_sql],
}
# history_meta - records each daily_update.py run so the read-only web tier
# can report freshness without recomputing MAX(date) across seven tables.
create_history_meta_table_sql = \
    ("CREATE TABLE IF NOT EXISTS history_meta ("
     "id INT AUTO_INCREMENT PRIMARY KEY, "
     "run_started DATETIME NOT NULL, "
     "run_finished DATETIME NULL, "
     "status ENUM('running','success','failed') NOT NULL, "
     "tables_json JSON NULL, "
     "error TEXT NULL)")

insert_history_meta_run_sql = \
    ("INSERT INTO history_meta (run_started, status) VALUES (%s, 'running')")

finish_history_meta_run_sql = \
    ("UPDATE history_meta SET run_finished = %s, status = 'success', "
     "tables_json = %s WHERE id = %s")

fail_history_meta_run_sql = \
    ("UPDATE history_meta SET run_finished = %s, status = 'failed', "
     "error = %s WHERE id = %s")

read_last_successful_run_query = \
    ("SELECT run_finished, tables_json FROM history_meta "
     "WHERE status = 'success' ORDER BY run_finished DESC LIMIT 1")

# current_prices - intraday price snapshot written by generators/price_snapshot.py
# so the read-only web tier never has to call yfinance for a live quote.
create_current_prices_table_sql = \
    ("CREATE TABLE IF NOT EXISTS current_prices ("
     "symbol VARCHAR(16) NOT NULL PRIMARY KEY, "
     "price DECIMAL(18, 4) NOT NULL, "
     "fetched_at DATETIME NOT NULL)")

replace_current_price_sql = \
    ("REPLACE INTO current_prices (symbol, price, fetched_at) "
     "VALUES (%s, %s, %s)")

read_current_prices_query = "SELECT symbol, price FROM current_prices"
read_current_prices_columns = ['Symbol', 'Current Price']

# Fallback for the read-only tier when a symbol has no snapshot yet (first
# deploy, or a symbol bought since the last snapshot run): its most recent
# closing price from assets_history.
#
# assets_history's PK is (date, symbol, account_type): a symbol held across
# multiple account types has one row per account on its latest date, so this
# must collapse those into a single row per symbol via GROUP BY -- otherwise
# a multi-account symbol comes back duplicated and doubles its Current Value
# after the merge in get_portfolio_current_value().
read_latest_closing_prices_query = \
    ("SELECT a.symbol, MAX(a.closing_price) AS closing_price "
     "FROM assets_history a "
     "INNER JOIN (SELECT symbol, MAX(date) AS max_date "
     "            FROM assets_history GROUP BY symbol) m "
     "  ON a.symbol = m.symbol AND a.date = m.max_date "
     "GROUP BY a.symbol")
read_latest_closing_prices_columns = ['Symbol', 'Current Price']
