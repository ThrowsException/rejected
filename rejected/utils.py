# -*- coding: utf-8 -*-
"""
Functions used mainly in startup and shutdown of rejected

"""
import logging
from logging import handlers
import os
from os import path
import signal
from socket import gethostname
import sys
import traceback
import yaml

# Windows doesn't support this
try:
    import pwd
    _SUPPORTS_PWD = True
except ImportError:
    _SUPPORTS_PWD = False

# Logger
_LOGGER = logging.getLogger('rejected.utils')

# Callback handlers
_REHASH_HANDLER = None
_SHUTDOWN_HANDLER = None

# Application state for shutdown
RUNNING = False

# Default group for files
_DEFAULT_GID = 1

# Set logging levels dictionary
LEVELS = {'debug':    logging.DEBUG,
          'info':     logging.INFO,
          'warning':  logging.WARNING,
          'error':    logging.ERROR,
          'critical': logging.CRITICAL}

# For mapping of log statements
_SIGNALS = {signal.SIGTERM: 'SIGTERM',
            signal.SIGHUP: 'SIGHUP',
            signal.SIGUSR1: 'SIGUSR1'}


def application_name():
    """Returns the currently running application name

    :returns: str

    """
    return path.split(sys.argv[0])[1]


def hostname():
    """Returns the hostname for the machine we're running on

    :returns: str

    """
    return gethostname().split(".")[0]


def daemonize(pidfile=None, user=None):
    """Fork the Python app into the background and close the appropriate
    file handles to detach from console. Based off of code by Jürgen Hermann
    at http://code.activestate.com/recipes/66012/

    :param pidfile: Filename to write the pidfile as
    :type pidfile: str
    :param user: Username to run, defaults as current user
    :type user: str
    :returns: bool

    """
    # Flush stdout and stderr
    sys.stdout.flush()
    sys.stderr.flush()

    # Get the user id if we have a user set
    if _SUPPORTS_PWD and user:
        uid = pwd.getpwnam(user).pw_uid
    else:
        uid = -1

    # Fork off from the process that called us
    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    # Second fork to put into daemon mode
    pid = os.fork()
    if pid > 0:
        # Setup a pidfile if we weren't passed one
        pidfile = pidfile or \
                  path.normpath('/tmp/%s-%i.pid' % (application_name(),
                                                       pid))

        # Write the pidfile out
        with open(pidfile, 'w') as pidfile_handle:
            pidfile_handle.write('%i\n' % pid)

            # If we have uid or gid change the uid for the file
            if uid > -1:
                os.fchown(pidfile_handle.fileno(), uid, _DEFAULT_GID)

        # Exit the parent process
        sys.exit(0)

    # Detach from parent environment
    os.chdir(os.path.normpath('/'))
    os.umask(0)
    os.setsid()

    # Redirect stdout, stderr, stdin
    stdin_ = file('/dev/null', 'r')
    stdout_ = file('/dev/null', 'a+')
    stderr_ = file('/dev/null', 'a+', 0)
    os.dup2(stdin_.fileno(), sys.stdin.fileno())
    os.dup2(stdout_.fileno(), sys.stdout.fileno())
    os.dup2(stderr_.fileno(), sys.stderr.fileno())

    # Set the running user
    if  user:
        logging.info("Changing the running user to %s", user)

    # If we have a uid and it's not for the running user
    if uid > -1 and uid != os.geteuid():
        try:
            os.seteuid(uid)
            _LOGGER.debug("User changed to %s(%i)", user, uid)
        except OSError as error:
            _LOGGER.error("Could not set the user: %s", error)

    return True


def load_configuration_file(config_file):
    """Load our YAML configuration file from disk or error out
    if not found or parsable

    :param config_file: Path to the configuration file we want to load
    :type config_file: str
    :returns: dict

    """
    try:
        with file(config_file, 'r') as config_file_handle:
            config = yaml.load(config_file_handle)

    except IOError as error:
        sys.stderr.write('Error when trying to read %s: %s\n' % (config_file,
                                                                 error))
        sys.exit(1)

    except yaml.scanner.ScannerError as error:
        sys.stderr.write('Invalid configuration file "%s":\n%s\n' % \
                         (config_file, error))
        sys.exit(1)

    return config


def set_logger_level(name, level):
    """Setup an individual logger at a specified level.

    :param name: Logger name
    :type name: str
    :param level: Logging level
    :type level: logging.LEVEL

    """
    logging.getLogger(name).setLevel(level)


def set_logging_level_for_loggers(loggers, level):
    """Iterate through our loggers if specified and set their levels.

    :param loggers: Loggers to set the level of
    :type loggers: list
    :param level: default logging level if not specified
    :type level: logging.LEVEL

    """
    # It's possible/probable there was nothing there.
    if not loggers:
        return

    # Apply the logging level to the loggers we have specifically set
    for logger in loggers:
        # If it's a list expect a logger_name, level string format
        if isinstance(logger, list):
            level_ = LEVELS.get(logger[1], logging.NOTSET)
            set_logger_level(logger[0], level_)

        # Otherwise we just just want a specific logger at the default level
        elif isinstance(logger, str):
            set_logger_level(logger, level)


def setup_logging(config, debug=False):
    """Setup the logging module to respect our configuration values.
    Passing in debug=True will disable any log output to anything but stdout
    and will set the log level to debug regardless of the config.

    :param config: The logging configuration
    :type dict: A dictionary of the following format:
        * directory:   Optional log file output directory
        * filename:    Optional filename, not needed for syslog
        * format:      Format for non-debug mode
        * level:       One of debug, error, warning, info
        * handler:     Optional handler
        * syslog:      If handler == syslog, parameters for syslog
          * address:   Syslog address
          * facility:  Syslog facility
        * loggers:     A list of logger
    :param debug: Debugging on?
    :type debug: bool

    """
    if debug:

        # Override the logging level to use debug mode
        config['level'] = 'debug'

        # If we have specified a file, remove it so logging info goes to stdout
        if 'filename' in config:
            del config['filename']

    # Use the configuration option for logging
    config['level'] = LEVELS.get(config['level'], logging.NOTSET)

    # Pass in our logging config
    logging.basicConfig(**config)

    # Get the default _logger
    default_logging = logging.getLogger()

    # Setup _loggers
    set_logging_level_for_loggers(config.get('loggers'), config['level'])

    # Remove the default stream handler
    stream_handler = None
    for handler in default_logging.handlers:
        if isinstance(handler, logging.StreamHandler):
            stream_handler = handler
            break

    # If we have supported handler
    if 'handler' in config:

        # If we want to syslog
        if config['handler'] == 'syslog':
            facility = config['syslog']['facility']

            # If we didn't type in the facility name
            if facility in handlers.SysLogHandler.facility_names:

                # Create the syslog handler
                address = config['syslog']['address']
                facility = handlers.SysLogHandler.facility_names[facility]
                syslog = handlers.SysLogHandler(address=address,
                                                facility=facility)
                # Add the handler
                default_logging.addHandler(syslog)

                # Remove the StreamHandler
                if stream_handler and not debug:
                    default_logging.removeHandler(stream_handler)
            else:
                logging.error('%s:Invalid facility, syslog logging aborted',
                              application_name())


def import_namespaced_class(namespaced_class):
    """Pass in a string in the format of foo.Bar, foo.bar.Baz, foo.bar.baz.Qux
    and it will return a handle to the class

    :param namespaced_class: The namespaced class
    :type namespaced_class: str
    :returns: class

    """
    # Split up our string containing the import and class
    parts = namespaced_class.split('.')

    # Build our strings for the import name and the class name
    import_name = '.'.join(parts[0:-1])
    class_name = parts[-1]

    # get the handle to the class for the given import
    class_handle = getattr(__import__(import_name, fromlist=class_name),
                           class_name)

    # Return the class handle
    return class_handle


def setup_signals():
    """Setup the signals we want to be notified on"""
    _LOGGER.info('Setting up signals for PID %i', os.getpid())
    for num in [signal.SIGTERM, signal.SIGUSR1, signal.SIGHUP]:
        signal.signal(num, signal_handler)


def signal_handler(signum, frame):
    """Handle signals that we have registered.

    :param signum: Signal passed in
    :type signum: int
    :param frame: The frame
    :type frame: the frame address

    """
    _LOGGER.info("Signal received: %s, %r", _SIGNALS[signum], frame)

    # Call the shutdown handler
    if signum == signal.SIGTERM and _SHUTDOWN_HANDLER:
        _LOGGER.debug('Calling shutdown handler: %r', _SHUTDOWN_HANDLER)
        _SHUTDOWN_HANDLER()

    # Call the rehash handler
    elif signum == signal.SIGHUP and _REHASH_HANDLER:
        _REHASH_HANDLER()

    # Handle USR1
    elif signum == signal.SIGUSR1:
        show_frames()

    # Odd we received a signal we don't support
    else:
        _LOGGER.info('No valid signal handler defined: %s', _SIGNALS[signum])


def rehash_handler(handler):
    """Specify the shutdown handler callback for when we receive a SIGHUP

    :param handler: The callback to call on SIGHUP
    :type hanlder: method or function

    """
    global _REHASH_HANDLER
    _REHASH_HANDLER = handler
    _LOGGER.debug('Rehash handler set to %r', handler)


def shutdown_handler(handler):
    """Specify the shutdown handler callback for when we receive a SIGTERM.

    :param handler: The callback to call on SIGTERM
    :type hanlder: method or function

    """
    global _SHUTDOWN_HANDLER
    _SHUTDOWN_HANDLER = handler
    _LOGGER.debug('Shutdown handler set to %r', handler)


def show_frames():
    """Log the current framestack to _LOGGER.info, called from SIG_USER1"""
    for stack in sys._current_frames().items():
        for filename, lineno, name, line in traceback.extract_stack(stack[1]):
            _LOGGER.info('  File: "%s", line %d, in %s', filename, lineno, name)
            if line:
                _LOGGER.info("    %s", line.strip())