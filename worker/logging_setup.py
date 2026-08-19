"""Logger configuration. Every other module just does getLogger('worker')."""

import logging
from logging.handlers import TimedRotatingFileHandler

from constants import LOG_FILE


def setup_logging():
    """
    Log to file and console at once. The file is what Task Scheduler runs
    leave behind; the console is for when you run it by hand.

    New file every midnight, two weeks kept - enough to dig into any recent
    failure without growing forever.
    """
    logger = logging.getLogger('worker')
    logger.setLevel(logging.INFO)

    if logger.handlers:  # already configured, don't stack handlers
        return logger

    file_handler = TimedRotatingFileHandler(
        LOG_FILE, when='midnight', backupCount=14, encoding='utf-8'
    )
    file_handler.setFormatter(
        logging.Formatter('%(asctime)s %(levelname)-7s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(levelname)-7s %(message)s'))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger