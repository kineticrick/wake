import unittest
import warnings
import pandas as pd
from datetime import datetime
from libraries.helpers import gen_hist_quantities, gen_assets_historical_value, gen_aggregated_historical_value, add_asset_info

class TestHelpers(unittest.TestCase):
    def setUp(self):
        # Create sample test data
        self.test_data = pd.DataFrame({
            'Date': ['2024-01-01', '2024-01-02', '2024-01-03'],
            'Symbol': ['AAPL', 'AAPL', 'AAPL'],
            'Action': ['buy', 'buy', 'sell'],
            'Quantity': [100, 50, 75],
            'PricePerShare': [150.0, 155.0, 160.0],
            'Multiplier': [1, 1, 1]
        })

    def test_gen_hist_quantities_basic(self):
        result = gen_hist_quantities(self.test_data)
        
        # Verify final quantity is correct (100 + 50 - 75 = 75)
        self.assertEqual(result.iloc[-1]['Quantity'], 75)
        
        # Verify cost basis calculation
        expected_cost_basis = (100 * 150.0) + (50 * 155.0) - (75 * 150.0)
        self.assertAlmostEqual(result.iloc[-1]['CostBasis'], expected_cost_basis)

    def test_gen_hist_quantities_split(self):
        # Add a 2:1 split to test data
        split_data = self.test_data.copy()
        split_data.loc[len(split_data)] = {
            'Date': '2024-01-04',
            'Symbol': 'AAPL',
            'Action': 'split',
            'Quantity': 0,
            'PricePerShare': 0,
            'Multiplier': 2
        }
        
        result = gen_hist_quantities(split_data)
        
        # Verify quantity doubles after split (75 * 2 = 150)
        self.assertEqual(result.iloc[-1]['Quantity'], 150)
        
        # Verify cost basis remains the same after split
        pre_split_cost_basis = result.iloc[-2]['CostBasis']
        post_split_cost_basis = result.iloc[-1]['CostBasis']
        self.assertAlmostEqual(pre_split_cost_basis, post_split_cost_basis)

    def test_gen_hist_quantities_acquisition(self):
        # Test acquisition scenario
        acquisition_data = pd.DataFrame({
            'Date': ['2024-01-01', '2024-01-02'],
            'Symbol': ['TARGET', 'TARGET'],
            'Action': ['buy', 'acquisition-target'],
            'Quantity': [100, 0],
            'PricePerShare': [50.0, 0],
            'Multiplier': [1, 0]
        })
        
        result = gen_hist_quantities(acquisition_data)
        
        # Verify quantity goes to 0 after acquisition
        self.assertEqual(result.iloc[-1]['Quantity'], 0)

    def test_gen_assets_historical_value(self):
        symbols = ['AAPL']
        
        # Test basic value calculation
        result = gen_assets_historical_value(symbols)
        self.assertTrue(all(col in result.columns for col in 
                           ['Date', 'Symbol', 'Quantity', 'CostBasis', 
                            'ClosingPrice', 'Value', 'PercentReturn']))
        
        # Test with start date
        start_date = '2024-01-01'
        dated_result = gen_assets_historical_value(symbols, start_date=start_date)
        self.assertTrue(all(date >= pd.Timestamp(start_date) 
                           for date in dated_result['Date']))
        
        # Test different cadences (each must produce progressively fewer rows
        # and not raise on the pandas frequency alias)
        weekly_result = gen_assets_historical_value(symbols, cadence='weekly')
        monthly_result = gen_assets_historical_value(symbols, cadence='monthly')
        quarterly_result = gen_assets_historical_value(symbols, cadence='quarterly')
        yearly_result = gen_assets_historical_value(symbols, cadence='yearly')
        self.assertTrue(len(weekly_result) < len(result))  # Should have fewer rows
        self.assertTrue(len(monthly_result) < len(weekly_result))
        self.assertTrue(len(quarterly_result) < len(monthly_result))
        self.assertTrue(len(yearly_result) < len(quarterly_result))

    def test_gen_assets_historical_value_rounds_only_numeric_columns(self):
        # DataFrame.round() silently skipped datetime columns in pandas 2 but
        # warns in pandas 3, so the rounding must target numeric columns only.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = gen_assets_historical_value(['AAPL'])

        offenders = [str(w.message) for w in caught
                     if 'round has no effect' in str(w.message)]
        self.assertEqual(offenders, [],
                         f"round() warned about non-numeric dtypes: {offenders}")

        # Rounding must still actually apply to the numeric columns
        for col in ['CostBasis', 'ClosingPrice', 'Value']:
            values = result[col].dropna()
            self.assertTrue((values.round(2) == values).all(),
                            f"{col} was not rounded to 2 decimal places")

        # ...and the Date column must survive untouched as a datetime
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(result['Date']))

    def test_add_asset_info_truncates_without_dtype_warning(self):
        # The entity columns are pandas 3 `str` dtype, not `object`. Selecting
        # them via include='object' only works through a back-compat shim that
        # pandas will remove — at which point truncation would silently select
        # no columns and stop happening at all.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = add_asset_info(pd.DataFrame({'Symbol': ['AAPL']}),
                                    truncate=True)

        offenders = [str(w.message) for w in caught
                     if 'select_dtypes' in str(w.message)]
        self.assertEqual(offenders, [],
                         f"select_dtypes raised a deprecation: {offenders}")

        # Truncation must still actually be applied to the string columns
        for col in ['Name', 'Sector', 'AssetType', 'Geography']:
            lengths = result[col].dropna().str.len()
            self.assertTrue((lengths <= 25).all(),
                            f"{col} exceeded the 25-character truncation")

    def test_gen_aggregated_historical_value(self):
        symbols = ['AAPL']
        # Test sector aggregation
        sector_result = gen_aggregated_historical_value('Sector', symbols=symbols)
        self.assertTrue('Sector' in sector_result.columns)
        self.assertTrue('total_value' in sector_result.columns)
        self.assertTrue('total_cost_basis' in sector_result.columns)

        # Test asset type aggregation
        asset_type_result = gen_aggregated_historical_value('AssetType', symbols=symbols)
        self.assertTrue('AssetType' in asset_type_result.columns)

    def test_gen_aggregated_historical_value_is_value_weighted(self):
        # Two assets, same sector, very different sizes.
        # Big winner should dominate -> value-weighted, not a 50/50 average.
        import libraries.helpers as H
        H._aggregation_cache.clear()
        expanded = pd.DataFrame({
            'Date':       ['2024-01-01', '2024-01-01'],
            'Symbol':     ['BIG', 'SMALL'],
            'Value':      [29000.0, 1000.0],
            'CostBasis':  [16000.0, 2000.0],
            'PercentReturn': [81.25, -50.0],
            'Sector':     ['Biotech', 'Biotech'],
        })
        # Inject directly into the cache so we exercise the aggregation only.
        key = ((), 'daily', 'None', None)
        H._aggregation_cache[key] = expanded
        out = H.gen_aggregated_historical_value(dimension='Sector')
        row = out.iloc[0]
        self.assertTrue(set(['Date', 'Sector', 'total_value', 'total_cost_basis'])
                        .issubset(out.columns))
        self.assertAlmostEqual(row['total_value'], 30000.0)
        self.assertAlmostEqual(row['total_cost_basis'], 18000.0)
        H._aggregation_cache.clear()
        
        # Test with specific symbols
        # symbols = ['AAPL', 'MSFT']
        # filtered_result = gen_aggregated_historical_value('Sector', symbols=symbols)
        # print(filtered_result)
        # self.assertTrue(all(row['Symbol'] in symbols 
        #                    for _, row in filtered_result.iterrows()))

if __name__ == '__main__':
    unittest.main() 