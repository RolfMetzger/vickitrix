#!/usr/bin/env python
"""
vickitrix

Checks tweets using http://www.tweepy.org/ and
uses rules specified in file to make market trades on GDAX using
https://github.com/danpaquin/GDAX-Python. Default rules are stored in 
rules/vicki.py and follow the tweets of @vickicryptobot.
"""
from __future__ import print_function

import sys

# For 2-3 compatibility
try:
    input = raw_input
except NameError:
    pass

_help_intro = """vickitrix allows users to base GDAX trades on tweets."""
_key_derivation_iterations = 5000

try:
    import gdax
except ImportError as e:
    e.message = (
         'vickitrix requires GDAX-Python. Install it with "pip install gdax".'
        )
    raise

try:
    import tweepy
except ImportError as e:
    e.message = (
            'vickitrix requires tweepy. Install it with '
            '"pip install tweepy".'
        )
    raise

try:
    from Crypto.Cipher import AES
    from Crypto.Protocol import KDF
    from Crypto import Random
except ImportError:
    e.message = (
        'vickitrix requires PyCrypto. Install it with '
        '"pip install pycrypto".'
    )
    raise

import os
import errno
import time
import argparse
import getpass
import datetime
import base64
import json
# In case user wants to use regular expressions on conditions/funds
import re

def help_formatter(prog):
    """ So formatter_class's max_help_position can be changed. """
    return argparse.HelpFormatter(prog, max_help_position=40)

def print_to_screen(message, newline=True, carriage_return=False):
    """ Prints message to stdout as well as stderr if stderr is redirected.

        message: message to print
        newline: True iff newline should be printed
        carriage_return: True iff carriage return should be printed; also
            clears line with ANSI escape code

        No return value.
    """
    full_message = ('\x1b[K' + message + ('\r' if carriage_return else '')
                        + ('\n' if newline else ''))
    try:
        sys.stderr.write(full_message)
        if sys.stderr.isatty():
            sys.stderr.flush()
        else:
            try:
                # So the user sees it too
                sys.stdout.write(full_message)
                sys.stdout.flush()
            except UnicodeEncodeError:
                sys.stdout.write(
                                unicodedata.normalize(
                                        'NFKD', full_message
                                    ).encode('ascii', 'ignore')
                            )
                sys.stdout.flush()
    except UnicodeEncodeError:
        sys.stderr.write(
                        unicodedata.normalize(
                                'NFKD', full_message
                            ).encode('ascii', 'ignore')
                    )
        sys.stderr.flush()

def timestamp():
    """ Returns timestamp string. """
    return time.strftime('%A, %b %d, %Y at %I:%M:%S %p %Z || ',
                         time.localtime(time.time()))

def prettify_dict(rule):
    """ Prettifies printout of dictionary as string.

        rule: rule

        Return value: rule string
    """
    return json.dumps(rule, sort_keys=True,
                        indent=4, separators=(',', ': '))

def get_dough(gdax_client, status_update=False):
    """ Retrieve dough in user accounts

        gdax_client: instance of gdax.AuthenticatedClient
        status_update: True iff status update should be printed

        Return value: dictionary mapping currency to account information
    """
    dough = {}
    for account in gdax_client.get_accounts():
        dough[account['currency']] = account['available']
    if status_update:
        print_to_screen(''.join([timestamp(), 'Available to trade: ',
                        ', '.join(map(' '.join,
                                        [el[::-1] for el in dough.items()]))]))
    return dough

class TradeListener(tweepy.StreamListener):
    """ Trades on GDAX based on tweets. """

    def __init__(self, rules, gdax_client, sleep_time=0.5, api=None):
        super(TradeListener, self).__init__(api=api)
        self.rules = rules
        self.gdax_client = gdax_client
        self.sleep_time = sleep_time
        self.available = get_dough(self.gdax_client, status_update=False)

    def on_status(self, status):
        for rule in self.rules:
            if ((not rule['handles'])
                 or status.author.screen_name in rule['handles']) and (
                 (not rule['keywords'])
                 or any([keyword in status.text
                            for keyword in rule['keywords']])) and eval(
                        rule['condition'].format(
                            tweet='status.text',
                            available=self.available
                    )):
                if ((hasattr(status, 'retweeted_status')
                     and status.retweeted_status)
                    or status.in_reply_to_status_id
                    or status.in_reply_to_status_id_str
                    or status.in_reply_to_user_id
                    or status.in_reply_to_user_id_str
                    or status.in_reply_to_screen_name):
                    # This is an RT or reply; don't do anything
                    return
                # Condition satisfied! Perform action
                print_to_screen(
                        ''.join(
                            [timestamp(), 'TWEET MATCHED || @',
                             status.author.screen_name, ': ', status.text]
                        )
                    )
                self.available = get_dough(self.gdax_client,
                                                status_update=True)
                not_enough = False
                for money in ['size', 'funds', 'price']:
                    try:
                        # If the hundredths rounds down to zero, ain't enough
                        rule['order'][money] = str(eval(
                                rule['order'][money].format(
                                        tweet='status.text',
                                        available=self.available
                                    )
                            ))
                        not_enough = (
                                int(float(rule['order'][money]) * 100) == 0
                            )
                    except KeyError:
                        pass
                print_to_screen(''.join(
                            [timestamp(), 'PLACING ORDER'] +
                            [prettify_dict(rule['order'])]
                        ))
                if not_enough:
                    print_to_screen(
                            timestamp() +
                            'One of {"price", "funds", "size"} is zero! ' +
                            'Order not placed.'
                        )
                    return
                if rule['order']['side'] == 'buy':
                    self.gdax_client.buy(**rule['order'])
                else:
                    assert rule['order']['side'] == 'sell'
                    self.gdax_client.sell(**rule['order'])
                print_to_screen(timestamp() + 'Order placed.')
                time.sleep(self.sleep_time)
                get_dough(self.gdax_client, status_update=True)

    def on_error(self, status_code):
        if status_code == 420:
            #returning False in on_data disconnects the stream
            return False

def go():
    """ Entry point """
    # Print file's docstring if -h is invoked
    parser = argparse.ArgumentParser(description=_help_intro, 
                formatter_class=help_formatter)
    subparsers = parser.add_subparsers(help=(
                'subcommands; add "-h" or "--help" '
                'after a subcommand for its parameters'),
                dest='subparser_name'
            )
    config_parser = subparsers.add_parser(
                            'configure',
                            help=(
                                'creates profile for storing keys/secrets; '
                                'all keys are stored in "{}".'.format(
                                        os.path.join(
                                            os.path.expanduser('~'),
                                            '.vickitrix',
                                            'config')
                                    )
                            )
                        )
    trade_parser = subparsers.add_parser(
                            'trade',
                            help='trades based on tweets'
                        )
    # Add command-line arguments
    trade_parser.add_argument('--profile', '-p', type=str, required=False,
            default='default',
            help='which profile to use for trading'
        )
    trade_parser.add_argument('--rules', '-r', type=str, required=False,
            default=os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                    'rules', 'vicki.py'),
            help=('rules file; this is Python that sets the variable "rules" '
                  'to a list of dictionaries')
        )
    trade_parser.add_argument('--interval', '-i', type=float, required=False,
            default=905,
            help=('how long to wait (in s) before reattempting to connect '
                  'after getting rate-limited')
        )
    trade_parser.add_argument('--sleep', '-s', type=float, required=False,
            default=0.5,
            help='how long to wait (in s) after a trade'
        )
    args = parser.parse_args()
    key_dir = os.path.join(os.path.expanduser('~'), '.vickitrix')
    if args.subparser_name == 'configure':
        try:
            os.makedirs(key_dir)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
        # Grab and write all necessary credentials
        config_file = os.path.join(key_dir, 'config')
        print('Enter a name for a new profile (default): ', end='')
        profile_name = input()
        if not profile_name: profile_name = 'default'
        salt = Random.new().read(AES.block_size)
        key = KDF.PBKDF2(getpass.getpass((
                'Enter a password for this profile. The password will be used '
                'to generate a key so all GDAX/Twitter passcodes/secrets '
                'written to {} are further encoded with AES256. '
                'You will have to enter a profile\'s password every time you '
                'run "vickitrix trade": '
            ).format(config_file)), salt,
                dkLen=32, count=_key_derivation_iterations)
        previous_lines_to_write = []
        if os.path.exists(config_file):
            '''Have to check if the profile exists already. If it does, replace
            it. Assume the config file is under vickitrix's control and thus 
            has no errors; if the user chooses to mess it up, that's on
            them.'''
            with open(config_file, 'rU') as config_stream:
                line = config_stream.readline().rstrip('\n')
                while line:
                    if line[0] == '[' and line[-1] == ']':
                        if profile_name == line[1:-1]:
                            # Skip this profile
                            for _ in range(8): config_stream.readline()
                            line = config_stream.readline().rstrip('\n')
                            continue
                        previous_lines_to_write.append(line)
                        for _ in range(8):
                            previous_lines_to_write.append(
                                        config_stream.readline().rstrip('\n')
                                    )
                    line = config_stream.readline().rstrip('\n')
        with open(config_file, 'w') as config_stream:
            print(''.join(['[', profile_name, ']']), file=config_stream)
        # Now change permissions
        try:
            os.chmod(config_file, 0o600)
        except OSError as e:
            if e.errno == errno.EPERM:
                print >>sys.stderr, (
                        ('Warning: could not change permissions of '
                         '"{}" so it\'s readable/writable by only the '
                         'current user. If there are other users of this '
                         'system, they may be able to read your credentials '
                         'file.').format(
                                config_file
                            )
                    )
                raise
        with open(config_file, 'a') as config_stream:
            print(''.join(['Salt: ', base64.b64encode(salt).decode()]),
                    file=config_stream)
            for token in ['GDAX key', 'GDAX secret', 'GDAX passphrase',
                            'Twitter consumer key', 'Twitter consumer secret',
                            'Twitter access token key',
                            'Twitter access token secret']:
                if 'key' in token:
                    print(''.join(['Enter ', token, ': ']), end='')
                    '''Write it in plaintext if it's a public key; then the 
                    user can open the config file and know which keys are in 
                    use.'''
                    print(''.join([token, ': ', input()]),
                            file=config_stream)
                else:
                    # A warning to developers in a variable name
                    unencoded_and_not_to_be_written_to_disk = getpass.getpass(
                                        ''.join(['Enter ', token, ': '])
                                    )
                    iv = Random.new().read(AES.block_size)
                    cipher = AES.new(key, AES.MODE_CFB, iv)
                    print(''.join([
                            token,
                            ' (AES256-encrypted using profile password): ',
                            base64.b64encode(iv + cipher.encrypt(
                                unencoded_and_not_to_be_written_to_disk
                            )).decode()]), file=config_stream)
            for line in previous_lines_to_write:
                print(line, file=config_stream)
        print(('Configured profile "{}". Encrypted credentials have been '
               'stored in "{}". '
               'Now use the "trade" subcommand to '
               'trigger trades with new tweets.').format(
                        profile_name,
                        config_file
                    ))
    elif args.subparser_name == 'trade':
        # Set and check rules
        from imp import load_source
        try:
            rules = load_source('rules', args.rules).rules
        except IOError as e:
            e.message = 'Cannot find or access rules file "{}".'.format(
                                                                    args.rules
                                                                )
            raise
        import copy
        # Add missing keys so listener doesn't fail
        new_rules = copy.copy(rules)
        order_vocab = set(['client_oid', 'type', 'side', 'product_id', 'stp',
                           'price', 'size', 'time_in_force', 'cancel_after',
                           'post_only', 'funds', 'overdraft_enabled',
                           'funding_amount'])
        for i, rule in enumerate(rules):
            # Check 'condition'
            try:
                eval(rule['condition'].format(
                        tweet='"The rain in Spain stays mainly in the plain."',
                        available={
                            'ETH' : .01,
                            'USD' : .01,
                            'LTC' : .01,
                            'BTC' : .01
                        }
                    ))
            except KeyError:
                # 'condition' isn't required, so make default True
                new_rules[i]['condition'] = 'True'
            except:
                raise RuntimeError(''.join([
                        ('"condition" from the following rule in the file '
                         '"{}" could not be '
                         'evaluated; check the format '
                         'and try again: ').format(args.rules),
                        os.linesep, prettify_dict(rule)
                    ])
                )
            # Check handles or keywords
            if 'handles' not in rule and 'keywords' not in rule:
                raise RuntimeError(''.join([
                        ('A rule must have at least one of {{"handles", '
                         '"keywords"}}, but this rule from the file "{}" '
                         'doesn\'t:').format(args.rules),
                        os.linesep, prettify_dict(rule)
                    ])
                )
            if 'handles' not in rule:
                new_rules[i]['handles'] = []
            if 'keywords' not in rule:
                new_rules[i]['keywords'] = []
            '''Validate order; follow https://docs.gdax.com/#orders for 
            filling in default values.'''
            if 'order' not in rule or not isinstance(rule['order'], dict):
                raise RuntimeError(''.join([
                        ('Every rule must have an "order" dictionary, but '
                         'this rule from the file "{}" doesn\'t:').format(
                        args.rules), os.linesep, prettify_dict(rule)
                    ])
                )
            unrecognized_keys = [
                    key for key in rule['order'] if key not in order_vocab
                ]
            if unrecognized_keys:
                raise RuntimeError(''.join([
                        'In the file "{}", the "order" key(s) '.format(
                            args.rules),
                        os.linesep, '[',
                        ', '.join(unrecognized_keys), ']', os.linesep,
                        'are not valid yet are present in the following rule:',
                        os.linesep, prettify_dict(rule)
                    ]))
            try:
                if rule['order']['type'] not in ['limit', 'market', 'stop']:
                    raise RuntimeError(''.join([
                        ('An order\'s "type" must be one of {{"limit", '
                         '"market", "stop"}}, which the order in this rule '
                         'from the file "{}" doesn\'t satisfy:').format(
                         args.rules), os.linesep, prettify_dict(rule)
                    ])
                )
            except KeyError:
                # GDAX default is limit
                new_rules[i]['order']['type'] = 'limit'
            if 'side' not in rule['order']:
                raise RuntimeError(''.join([
                        ('An order must have a "side", but the order in '
                         'this rule from the file "{}" doesn\'t:').format(
                         args.rules), os.linesep, prettify_dict(rule)
                    ])
                )
            if rule['order']['side'] not in ['buy', 'sell']:
                    raise RuntimeError(''.join([
                        ('An order\'s "side" must be one of {{"buy", '
                         '"sell"}}, which the order in this rule '
                         'from the file "{}" doesn\'t satisfy:').format(
                         args.rules), os.linesep, prettify_dict(rule)
                    ])
                )
            if 'product_id' not in rule['order']:
                raise RuntimeError(''.join([
                        ('An order must have a "product_id", but in the file '
                         '"{}", this rule\'s order doesn\'t:').format(
                         args.rules), os.linesep, prettify_dict(rule)
                    ])
                )
            if new_rules[i]['order']['type'] == 'limit':
                for item in ['price', 'size']:
                    if item not in rule['order']:
                        raise RuntimeError(''.join([
                            ('If an order\'s "type" is "limit", the order '
                             'must specify a "{}", but in the file "{}", this '
                             'rule\'s order doesn\'t:').format(
                             item, args.rules),
                             os.linesep, prettify_dict(rule)
                        ]))
            elif new_rules[i]['order']['type'] in ['market', 'stop']:
                if ('size' not in rule['order']
                     and 'funds' not in rule['order']):
                    raise RuntimeError(''.join([
                            ('If an order\'s "type" is "{}", the order '
                             'must have at least one of {{"size", '
                             '"funds"}}, but in file "{}", this rule\'s '
                             'order doesn\'t:').format(
                                    new_rules[i]['order']['type'], args.rules
                                ),
                            os.linesep, prettify_dict(rule)
                        ])
                    )
                for stack in ['size', 'funds']:
                    try:
                        eval(rule['order'][stack].format(
                            tweet=('"The rain in Spain stays mainly '
                                   'in the plain."'),
                            available={
                                'ETH' : .01,
                                'USD' : .01,
                                'LTC' : .01,
                                'BTC' : .01
                            }))
                    except KeyError:
                        pass
                    except Exception as e:
                        raise RuntimeError(''.join([
                                ('"{}" from the following rule in the file '
                                 '"{}" could not be '
                                 'evaluated; check the format '
                                 'and try again: ').format(stack, args.rules),
                                os.linesep, prettify_dict(rule)
                            ])
                        )
        rules = new_rules
        # Use _last_ entry in config file with profile name
        key = None
        try:
            with open(os.path.join(key_dir, 'config'), 'rU') as config_stream:
                line = config_stream.readline().rstrip('\n')
                while line:
                    profile_name = line[1:-1]
                    if profile_name == args.profile:
                        salt = base64.b64decode(
                                config_stream.readline().rstrip(
                                        '\n').partition(': ')[2]
                            )
                        if key is None:
                            key = KDF.PBKDF2(getpass.getpass(
                                    'Enter password for profile "{}": '.format(
                                                                profile_name
                                                            )
                                ), salt,
                                dkLen=32, count=_key_derivation_iterations
                            )
                        keys_and_secrets = []
                        for _ in range(7):
                            item, _, encoded = config_stream.readline().rstrip(
                                                    '\n').partition(': ')
                            if 'key' in item:
                                # Not actually encoded; remove leading space
                                keys_and_secrets.append(encoded)
                                continue
                            encoded = base64.b64decode(encoded)
                            cipher = AES.new(
                                    key, AES.MODE_CFB,
                                    encoded[:AES.block_size]
                                )
                            keys_and_secrets.append(
                                    cipher.decrypt(
                                            encoded
                                        )[AES.block_size:]
                                )
                    else:
                        # Skip profile
                        for _ in range(8): config_stream.readline()
                    line = config_stream.readline().rstrip('\n')
        except IOError as e:
            e.message = (
                    'Cannot find vickitrix config file. Use '
                    '"vickitrix configure" to configure vickitrix '
                    'before trading.'
                )
            raise
        try:
            # Instantiate GDAX and Twitter clients
            gdax_client = gdax.AuthenticatedClient(
                                    *keys_and_secrets[:3]
                                )
            # Are they working?
            get_dough(gdax_client, status_update=True)
            auth = tweepy.OAuthHandler(*keys_and_secrets[3:5])
            auth.set_access_token(*keys_and_secrets[5:7])
            twitter_client = tweepy.API(auth)
        except Exception as e:
            from traceback import format_exc
            print_to_screen(format_exc())
            print_to_screen(''.join(
                    [os.linesep,
                     'Chances are, this opaque error happened because either ',
                      os.linesep,
                      'a) You entered incorrect security credentials '
                      'when you were configuring vickitrix.',
                      os.linesep,
                      'b) You entered the wrong password above.']
                ))
            exit(1)
        # Get all handles to monitor
        handles, keywords = set(), set()
        for rule in rules:
            handles.update(rule['handles'])
            keywords.update(rule['keywords'])
        handles_to_user_ids = {}
        for handle in handles:
            try:
                handles_to_user_ids[handle] = twitter_client.get_user(
                                                                handle
                                                            ).id_str
            except tweepy.error.TweepError as e:
                if 'User not found' in e:
                    'Handle {} not found; skipping...'.format(handle)
        if not handles_to_user_ids:
            raise RuntimeError('No followable Twitter handles found in rules!')
        trade_listener = TradeListener(rules, gdax_client)
        stream = tweepy.Stream(
                    auth=twitter_client.auth, listener=trade_listener,
                    sleep_time=args.sleep
                )
        while True:
            print_to_screen('Listening for tweets; hit CTRL+C to quit...')
            stream.filter(follow=handles_to_user_ids.values(),
                            track=list(keywords))
            print_to_screen(
                    timestamp()
                    + 'Rate limit error. Restarting in {} s...'.format(
                                                                args.interval
                                                            )
                )
            time.sleep(args.interval)
