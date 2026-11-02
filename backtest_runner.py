"""Manual research CLI. Has no Telegram or GitHub write path."""
import argparse
import json
from pathlib import Path
from backtest import config as configuration, data as inputs, signals, events, portfolio, report
from fundamentals.config import load_config as fundamental_config
from investment.config import load_config as investment_config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='JSON settings; omitted values use backtest_config.json')
    parser.add_argument('--mode', choices=['event', 'portfolio', 'both'], default='event')
    parser.add_argument('--offline', action='store_true', help='Use local verified import/cache only')
    parser.add_argument('--collect', action='store_true', help='Fetch explicitly configured sample symbols')
    parser.add_argument('--demo', action='store_true', help='Fabricated deterministic demonstration')
    parser.add_argument('--data-file')
    parser.add_argument('--output-dir')
    args = parser.parse_args(argv)
    if args.offline and args.collect or args.demo and args.collect:
        parser.error('--collect cannot be combined with --offline or --demo')
    config = configuration.load(args.config)
    if args.data_file:
        config['data_file'] = args.data_file
    if args.output_dir:
        config['output_dir'] = args.output_dir
    root = Path(__file__).resolve().parent
    policies = {'fundamental': fundamental_config(), 'investment': investment_config(),
                'dividend': json.loads((root / 'dividend_config.json').read_text(encoding='utf-8-sig'))}
    errors = []
    if args.demo:
        from backtest.demo import bundle, configuration as demo_config
        data, config = bundle(), demo_config(config)
    elif args.collect:
        from backtest.collection import collect
        if not config.get('collect'):
            parser.error('Configure collect: [{"symbol":"AAPL","market":"us"}, ...] first')
        data, errors = collect(config)
    elif Path(config['data_file']).exists():
        data = inputs.load(config['data_file'])
    else:
        parser.error('Historical input missing. Use --demo or supply --data-file; current scores are not historical data.')
    inputs.validate(data)
    for experiment in configuration.experiments(config):
        rows, signal_events = signals.evaluate(data, experiment, policies)
        result = [r for sample in ('all', 'common') for r in events.run(data, signal_events, experiment, sample)]
        portfolios = portfolio.run(data, signal_events, experiment, rows) if args.mode in ('portfolio', 'both') else []
        folder = report.save(data, experiment, policies, rows, result, portfolios, errors)
        print(f'Report: {folder / "report.html"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
