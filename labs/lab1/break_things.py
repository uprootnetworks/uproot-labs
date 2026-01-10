import argparse
import sys
import logging
import os

from os import environ as env
from dotenv import find_dotenv, load_dotenv


ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)


def parse_args():
    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter)
    network = parser.add_argument_group ('NETWORK')

    network.add_argument("-a", "--all", action="store_true", help="Reconfigures all available options - a.k.a breaks everything :)")
    network.add_argument("-s", "--switch", action="store_true", help="Adds incorrect configuration to Branch Switch1")
    network.add_argument("-r", "--router", action="store_true", help="Reconfigures SP-Router1 and SP-Router2 with random faults")
    network.add_argument("-f", "--firewall", action="store_true", help="Reconfigures Branch-FW and App-FW with random faults")

    network.add_argument("-d", "--default", action="store_true", help="Restores all nodes to default settings")

    args = parser.parse_args(args=None if sys.argv[1:] else ['--help'])
    return args



def main(args):
    from pkg.module_runner import run as mr
    mr(args)


def setup_logging():
    level_name = os.getenv("UPROOT_LOG_LEVEL", "INFO").upper()

    level = logging.INFO
    if isinstance(level_name, str):
        level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        )

setup_logging()
args=parse_args()
main(args)
