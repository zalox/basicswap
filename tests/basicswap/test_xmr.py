#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright (c) 2020-2022 tecnovert
# Distributed under the MIT software license, see the accompanying
# file LICENSE or http://www.opensource.org/licenses/mit-license.php.

import os
import json
import time
import random
import shutil
import signal
import logging
import unittest
import traceback
import threading
import subprocess

import basicswap.config as cfg
from basicswap.db import (
    Concepts,
)
from basicswap.basicswap import (
    Coins,
    BasicSwap,
    BidStates,
    SwapTypes,
    DebugTypes,
)
from basicswap.basicswap_util import (
    TxLockTypes,
)
from basicswap.util import (
    COIN,
    make_int,
    format_amount,
)
from basicswap.util.address import (
    toWIF,
)
from basicswap.rpc import (
    callrpc,
    callrpc_cli,
    waitForRPC,
)
from basicswap.rpc_xmr import (
    callrpc_xmr,
    callrpc_xmr_na,
)
from basicswap.interface.xmr import (
    XMR_COIN,
)
from basicswap.contrib.key import (
    ECKey,
)
from basicswap.http_server import (
    HttpThread,
)
from tests.basicswap.common import (
    prepareDataDir,
    make_rpc_func,
    checkForks,
    stopDaemons,
    wait_for_bid,
    wait_for_event,
    wait_for_offer,
    wait_for_no_offer,
    wait_for_none_active,
    wait_for_balance,
    post_json_req,
    read_json_api,
    compare_bid_states,
    extract_states_from_xu_file,
    TEST_HTTP_HOST,
    TEST_HTTP_PORT,
    BASE_RPC_PORT,
    BASE_ZMQ_PORT,
    BTC_BASE_PORT,
    BTC_BASE_RPC_PORT,
    LTC_BASE_PORT,
    LTC_BASE_RPC_PORT,
    PIVX_BASE_PORT,
    PIVX_BASE_RPC_PORT,
    PREFIX_SECRET_KEY_REGTEST,
)
from bin.basicswap_run import startDaemon, startXmrDaemon


logger = logging.getLogger()

NUM_NODES = 3
NUM_XMR_NODES = 3
NUM_BTC_NODES = 3
NUM_LTC_NODES = 3
NUM_PIVX_NODES = 3
TEST_DIR = cfg.TEST_DATADIRS

XMR_BASE_P2P_PORT = 17792
XMR_BASE_RPC_PORT = 21792
XMR_BASE_ZMQ_PORT = 22792
XMR_BASE_WALLET_RPC_PORT = 23792

test_delay_event = threading.Event()


def prepareXmrDataDir(datadir, node_id, conf_file):
    node_dir = os.path.join(datadir, 'xmr_' + str(node_id))
    if not os.path.exists(node_dir):
        os.makedirs(node_dir)
    cfg_file_path = os.path.join(node_dir, conf_file)
    if os.path.exists(cfg_file_path):
        return
    with open(cfg_file_path, 'w+') as fp:
        fp.write('regtest=1\n')
        fp.write('keep-fakechain=1\n')
        fp.write('data-dir={}\n'.format(node_dir))
        fp.write('fixed-difficulty=1\n')
        # fp.write('offline=1\n')
        fp.write('p2p-bind-port={}\n'.format(XMR_BASE_P2P_PORT + node_id))
        fp.write('rpc-bind-port={}\n'.format(XMR_BASE_RPC_PORT + node_id))
        fp.write('p2p-bind-ip=127.0.0.1\n')
        fp.write('rpc-bind-ip=127.0.0.1\n')
        fp.write('prune-blockchain=1\n')
        fp.write('zmq-rpc-bind-port={}\n'.format(XMR_BASE_ZMQ_PORT + node_id))
        fp.write('zmq-rpc-bind-ip=127.0.0.1\n')

        for i in range(0, NUM_XMR_NODES):
            if node_id == i:
                continue
            fp.write('add-exclusive-node=127.0.0.1:{}\n'.format(XMR_BASE_P2P_PORT + i))


def startXmrWalletRPC(node_dir, bin_dir, wallet_bin, node_id, opts=[]):
    daemon_bin = os.path.expanduser(os.path.join(bin_dir, wallet_bin))

    data_dir = os.path.expanduser(node_dir)
    args = [daemon_bin]
    args += ['--non-interactive']
    args += ['--daemon-address=127.0.0.1:{}'.format(XMR_BASE_RPC_PORT + node_id)]
    args += ['--no-dns']
    args += ['--rpc-bind-port={}'.format(XMR_BASE_WALLET_RPC_PORT + node_id)]
    args += ['--wallet-dir={}'.format(os.path.join(data_dir, 'wallets'))]
    args += ['--log-file={}'.format(os.path.join(data_dir, 'wallet.log'))]
    args += ['--rpc-login=test{0}:test_pass{0}'.format(node_id)]
    args += ['--shared-ringdb-dir={}'.format(os.path.join(data_dir, 'shared-ringdb'))]

    args += opts
    logging.info('Starting daemon {} --wallet-dir={}'.format(daemon_bin, node_dir))

    wallet_stdout = open(os.path.join(data_dir, 'wallet_stdout.log'), 'w')
    wallet_stderr = open(os.path.join(data_dir, 'wallet_stderr.log'), 'w')
    return subprocess.Popen(args, stdin=subprocess.PIPE, stdout=wallet_stdout, stderr=wallet_stderr, cwd=data_dir)


def prepare_swapclient_dir(datadir, node_id, network_key, network_pubkey, with_coins=set()):
    basicswap_dir = os.path.join(datadir, 'basicswap_' + str(node_id))
    if not os.path.exists(basicswap_dir):
        os.makedirs(basicswap_dir)

    settings_path = os.path.join(basicswap_dir, cfg.CONFIG_FILENAME)
    settings = {
        'debug': True,
        'zmqhost': 'tcp://127.0.0.1',
        'zmqport': BASE_ZMQ_PORT + node_id,
        'htmlhost': '127.0.0.1',
        'htmlport': TEST_HTTP_PORT + node_id,
        'network_key': network_key,
        'network_pubkey': network_pubkey,
        'chainclients': {
            'particl': {
                'connection_type': 'rpc',
                'manage_daemon': False,
                'rpcport': BASE_RPC_PORT + node_id,
                'rpcuser': 'test' + str(node_id),
                'rpcpassword': 'test_pass' + str(node_id),
                'datadir': os.path.join(datadir, 'part_' + str(node_id)),
                'bindir': cfg.PARTICL_BINDIR,
                'blocks_confirmed': 2,  # Faster testing
                'anon_tx_ring_size': 5,  # Faster testing
            },
            'bitcoin': {
                'connection_type': 'rpc',
                'manage_daemon': False,
                'rpcport': BTC_BASE_RPC_PORT + node_id,
                'rpcuser': 'test' + str(node_id),
                'rpcpassword': 'test_pass' + str(node_id),
                'datadir': os.path.join(datadir, 'btc_' + str(node_id)),
                'bindir': cfg.BITCOIN_BINDIR,
                'use_segwit': True,
            }
        },
        'check_progress_seconds': 2,
        'check_watched_seconds': 4,
        'check_expired_seconds': 60,
        'check_events_seconds': 1,
        'check_xmr_swaps_seconds': 1,
        'min_delay_event': 1,
        'max_delay_event': 5,
        'min_delay_event_short': 1,
        'max_delay_event_short': 5,
        'min_delay_retry': 2,
        'max_delay_retry': 10,
        'debug_ui': True,
    }

    if Coins.XMR in with_coins:
        settings['chainclients']['monero'] = {
            'connection_type': 'rpc',
            'manage_daemon': False,
            'rpcport': XMR_BASE_RPC_PORT + node_id,
            'walletrpcport': XMR_BASE_WALLET_RPC_PORT + node_id,
            'walletrpcuser': 'test' + str(node_id),
            'walletrpcpassword': 'test_pass' + str(node_id),
            'walletfile': 'testwallet',
            'datadir': os.path.join(datadir, 'xmr_' + str(node_id)),
            'bindir': cfg.XMR_BINDIR,
        }

    if Coins.LTC in with_coins:
        settings['chainclients']['litecoin'] = {
            'connection_type': 'rpc',
            'manage_daemon': False,
            'rpcport': LTC_BASE_RPC_PORT + node_id,
            'rpcuser': 'test' + str(node_id),
            'rpcpassword': 'test_pass' + str(node_id),
            'datadir': os.path.join(datadir, 'ltc_' + str(node_id)),
            'bindir': cfg.LITECOIN_BINDIR,
            'use_segwit': True,
        }

    if Coins.PIVX in with_coins:
        settings['chainclients']['pivx'] = {
            'connection_type': 'rpc',
            'manage_daemon': False,
            'rpcport': PIVX_BASE_RPC_PORT + node_id,
            'rpcuser': 'test' + str(node_id),
            'rpcpassword': 'test_pass' + str(node_id),
            'datadir': os.path.join(datadir, 'pivx_' + str(node_id)),
            'bindir': cfg.PIVX_BINDIR,
            'use_segwit': False,
        }

    with open(settings_path, 'w') as fp:
        json.dump(settings, fp, indent=4)


def btcCli(cmd, node_id=0):
    return callrpc_cli(cfg.BITCOIN_BINDIR, os.path.join(TEST_DIR, 'btc_' + str(node_id)), 'regtest', cmd, cfg.BITCOIN_CLI)


def ltcCli(cmd, node_id=0):
    return callrpc_cli(cfg.LITECOIN_BINDIR, os.path.join(TEST_DIR, 'ltc_' + str(node_id)), 'regtest', cmd, cfg.LITECOIN_CLI)


def pivxCli(cmd, node_id=0):
    return callrpc_cli(cfg.PIVX_BINDIR, os.path.join(TEST_DIR, 'pivx_' + str(node_id)), 'regtest', cmd, cfg.PIVX_CLI)


def signal_handler(sig, frame):
    logging.info('signal {} detected.'.format(sig))
    test_delay_event.set()


def waitForXMRNode(rpc_offset, max_tries=7):
    for i in range(max_tries + 1):
        try:
            callrpc_xmr_na(XMR_BASE_RPC_PORT + rpc_offset, 'get_block_count')
            return
        except Exception as ex:
            if i < max_tries:
                logging.warning('Can\'t connect to XMR RPC: %s. Retrying in %d second/s.', str(ex), (i + 1))
                time.sleep(i + 1)
    raise ValueError('waitForXMRNode failed')


def waitForXMRWallet(rpc_offset, auth, max_tries=7):
    for i in range(max_tries + 1):
        try:
            callrpc_xmr(XMR_BASE_WALLET_RPC_PORT + rpc_offset, auth, 'get_languages')
            return
        except Exception as ex:
            if i < max_tries:
                logging.warning('Can\'t connect to XMR wallet RPC: %s. Retrying in %d second/s.', str(ex), (i + 1))
                time.sleep(i + 1)
    raise ValueError('waitForXMRWallet failed')


def callnoderpc(node_id, method, params=[], wallet=None, base_rpc_port=BASE_RPC_PORT):
    auth = 'test{0}:test_pass{0}'.format(node_id)
    return callrpc(base_rpc_port + node_id, auth, method, params, wallet)


pause_event = threading.Event()


def run_coins_loop(cls):
    while not test_delay_event.is_set():
        pause_event.wait()
        try:
            if cls.btc_addr is not None:
                btcCli('generatetoaddress 1 {}'.format(cls.btc_addr))
            if cls.ltc_addr is not None:
                ltcCli('generatetoaddress 1 {}'.format(cls.ltc_addr))
            if cls.pivx_addr is not None:
                pivxCli('generatetoaddress 1 {}'.format(cls.pivx_addr))
            if cls.xmr_addr is not None:
                callrpc_xmr_na(XMR_BASE_RPC_PORT + 1, 'generateblocks', {'wallet_address': cls.xmr_addr, 'amount_of_blocks': 1})
        except Exception as e:
            logging.warning('run_coins_loop ' + str(e))
        test_delay_event.wait(1.0)


def run_loop(cls):
    while not test_delay_event.is_set():
        for c in cls.swap_clients:
            c.update()
        test_delay_event.wait(1.0)


class BaseTest(unittest.TestCase):
    __test__ = False

    @classmethod
    def setUpClass(cls):
        if not hasattr(cls, 'start_ltc_nodes'):
            cls.start_ltc_nodes = False
        if not hasattr(cls, 'start_pivx_nodes'):
            cls.start_pivx_nodes = False
        if not hasattr(cls, 'start_xmr_nodes'):
            cls.start_xmr_nodes = True

        random.seed(time.time())

        cls.update_thread = None
        cls.coins_update_thread = None
        cls.http_threads = []
        cls.swap_clients = []
        cls.part_daemons = []
        cls.btc_daemons = []
        cls.ltc_daemons = []
        cls.pivx_daemons = []
        cls.xmr_daemons = []
        cls.xmr_wallet_auth = []

        cls.xmr_addr = None
        cls.btc_addr = None
        cls.ltc_addr = None
        cls.pivx_addr = None

        logger.propagate = False
        logger.handlers = []
        logger.setLevel(logging.INFO)  # DEBUG shows many messages from requests.post
        formatter = logging.Formatter('%(asctime)s %(levelname)s : %(message)s')
        stream_stdout = logging.StreamHandler()
        stream_stdout.setFormatter(formatter)
        logger.addHandler(stream_stdout)

        if os.path.isdir(TEST_DIR):
            logging.info('Removing ' + TEST_DIR)
            for name in os.listdir(TEST_DIR):
                if name == 'pivx-params':
                    continue
                fullpath = os.path.join(TEST_DIR, name)
                if os.path.isdir(fullpath):
                    shutil.rmtree(fullpath)
                else:
                    os.remove(fullpath)
        if not os.path.exists(TEST_DIR):
            os.makedirs(TEST_DIR)

        cls.stream_fp = logging.FileHandler(os.path.join(TEST_DIR, 'test.log'))
        cls.stream_fp.setFormatter(formatter)
        logger.addHandler(cls.stream_fp)

        diagrams_dir = 'doc/protocols/sequence_diagrams'
        cls.states_bidder = extract_states_from_xu_file(os.path.join(diagrams_dir, 'xmr.bidder.alt.xu'), 'B')
        cls.states_offerer = extract_states_from_xu_file(os.path.join(diagrams_dir, 'xmr.offerer.alt.xu'), 'O')

        try:
            logging.info('Preparing coin nodes.')
            for i in range(NUM_NODES):
                data_dir = prepareDataDir(TEST_DIR, i, 'particl.conf', 'part_')
                if os.path.exists(os.path.join(cfg.PARTICL_BINDIR, 'particl-wallet')):
                    callrpc_cli(cfg.PARTICL_BINDIR, data_dir, 'regtest', '-wallet=wallet.dat create', 'particl-wallet')

                cls.part_daemons.append(startDaemon(os.path.join(TEST_DIR, 'part_' + str(i)), cfg.PARTICL_BINDIR, cfg.PARTICLD))
                logging.info('Started %s %d', cfg.PARTICLD, cls.part_daemons[-1].pid)

            for i in range(NUM_NODES):
                # Load mnemonics after all nodes have started to avoid staking getting stuck in TryToSync
                rpc = make_rpc_func(i)
                waitForRPC(rpc)
                if i == 0:
                    rpc('extkeyimportmaster', ['abandon baby cabbage dad eager fabric gadget habit ice kangaroo lab absorb'])
                elif i == 1:
                    rpc('extkeyimportmaster', ['pact mammal barrel matrix local final lecture chunk wasp survey bid various book strong spread fall ozone daring like topple door fatigue limb olympic', '', 'true'])
                    rpc('getnewextaddress', ['lblExtTest'])
                    rpc('rescanblockchain')
                else:
                    rpc('extkeyimportmaster', [rpc('mnemonic', ['new'])['master']])
                # Lower output split threshold for more stakeable outputs
                rpc('walletsettings', ['stakingoptions', {'stakecombinethreshold': 100, 'stakesplitthreshold': 200}])

            for i in range(NUM_BTC_NODES):
                data_dir = prepareDataDir(TEST_DIR, i, 'bitcoin.conf', 'btc_', base_p2p_port=BTC_BASE_PORT, base_rpc_port=BTC_BASE_RPC_PORT)
                if os.path.exists(os.path.join(cfg.BITCOIN_BINDIR, 'bitcoin-wallet')):
                    callrpc_cli(cfg.BITCOIN_BINDIR, data_dir, 'regtest', '-wallet=wallet.dat create', 'bitcoin-wallet')

                cls.btc_daemons.append(startDaemon(os.path.join(TEST_DIR, 'btc_' + str(i)), cfg.BITCOIN_BINDIR, cfg.BITCOIND))
                logging.info('Started %s %d', cfg.BITCOIND, cls.part_daemons[-1].pid)

                waitForRPC(make_rpc_func(i, base_rpc_port=BTC_BASE_RPC_PORT))

            if cls.start_ltc_nodes:
                for i in range(NUM_LTC_NODES):
                    data_dir = prepareDataDir(TEST_DIR, i, 'litecoin.conf', 'ltc_', base_p2p_port=LTC_BASE_PORT, base_rpc_port=LTC_BASE_RPC_PORT)
                    if os.path.exists(os.path.join(cfg.LITECOIN_BINDIR, 'litecoin-wallet')):
                        callrpc_cli(cfg.LITECOIN_BINDIR, data_dir, 'regtest', '-wallet=wallet.dat create', 'litecoin-wallet')

                    cls.ltc_daemons.append(startDaemon(os.path.join(TEST_DIR, 'ltc_' + str(i)), cfg.LITECOIN_BINDIR, cfg.LITECOIND))
                    logging.info('Started %s %d', cfg.LITECOIND, cls.part_daemons[-1].pid)

                    waitForRPC(make_rpc_func(i, base_rpc_port=LTC_BASE_RPC_PORT))

            if cls.start_pivx_nodes:
                for i in range(NUM_PIVX_NODES):
                    data_dir = prepareDataDir(TEST_DIR, i, 'pivx.conf', 'pivx_', base_p2p_port=PIVX_BASE_PORT, base_rpc_port=PIVX_BASE_RPC_PORT)
                    if os.path.exists(os.path.join(cfg.PIVX_BINDIR, 'pivx-wallet')):
                        callrpc_cli(cfg.PIVX_BINDIR, data_dir, 'regtest', '-wallet=wallet.dat create', 'pivx-wallet')

                    cls.pivx_daemons.append(startDaemon(os.path.join(TEST_DIR, 'pivx_' + str(i)), cfg.PIVX_BINDIR, cfg.PIVXD))
                    logging.info('Started %s %d', cfg.PIVXD, cls.part_daemons[-1].pid)

                    waitForRPC(make_rpc_func(i, base_rpc_port=PIVX_BASE_RPC_PORT))

            if cls.start_xmr_nodes:
                for i in range(NUM_XMR_NODES):
                    prepareXmrDataDir(TEST_DIR, i, 'monerod.conf')

                    cls.xmr_daemons.append(startXmrDaemon(os.path.join(TEST_DIR, 'xmr_' + str(i)), cfg.XMR_BINDIR, cfg.XMRD))
                    logging.info('Started %s %d', cfg.XMRD, cls.xmr_daemons[-1].pid)
                    waitForXMRNode(i)

                    cls.xmr_daemons.append(startXmrWalletRPC(os.path.join(TEST_DIR, 'xmr_' + str(i)), cfg.XMR_BINDIR, cfg.XMR_WALLET_RPC, i))

                for i in range(NUM_XMR_NODES):
                    cls.xmr_wallet_auth.append(('test{0}'.format(i), 'test_pass{0}'.format(i)))
                    logging.info('Creating XMR wallet %i', i)

                    waitForXMRWallet(i, cls.xmr_wallet_auth[i])

                    cls.callxmrnodewallet(cls, i, 'create_wallet', {'filename': 'testwallet', 'language': 'English'})
                    cls.callxmrnodewallet(cls, i, 'open_wallet', {'filename': 'testwallet'})

            logging.info('Preparing swap clients.')
            eckey = ECKey()
            eckey.generate()
            cls.network_key = toWIF(PREFIX_SECRET_KEY_REGTEST, eckey.get_bytes())
            cls.network_pubkey = eckey.get_pubkey().get_bytes().hex()

            for i in range(NUM_NODES):
                start_nodes = set()
                if cls.start_ltc_nodes:
                    start_nodes.add(Coins.LTC)
                if cls.start_xmr_nodes:
                    start_nodes.add(Coins.XMR)
                if cls.start_pivx_nodes:
                    start_nodes.add(Coins.PIVX)
                prepare_swapclient_dir(TEST_DIR, i, cls.network_key, cls.network_pubkey, start_nodes)
                basicswap_dir = os.path.join(os.path.join(TEST_DIR, 'basicswap_' + str(i)))
                settings_path = os.path.join(basicswap_dir, cfg.CONFIG_FILENAME)
                with open(settings_path) as fs:
                    settings = json.load(fs)
                fp = open(os.path.join(basicswap_dir, 'basicswap.log'), 'w')
                sc = BasicSwap(fp, basicswap_dir, settings, 'regtest', log_name='BasicSwap{}'.format(i))
                sc.setDaemonPID(Coins.BTC, cls.btc_daemons[i].pid)
                sc.setDaemonPID(Coins.PART, cls.part_daemons[i].pid)

                if cls.start_ltc_nodes:
                    sc.setDaemonPID(Coins.LTC, cls.ltc_daemons[i].pid)

                sc.start()
                if cls.start_xmr_nodes:
                    # Set XMR main wallet address
                    xmr_ci = sc.ci(Coins.XMR)
                    sc.setStringKV('main_wallet_addr_' + xmr_ci.coin_name().lower(), xmr_ci.getMainWalletAddress())
                cls.swap_clients.append(sc)

                t = HttpThread(cls.swap_clients[i].fp, TEST_HTTP_HOST, TEST_HTTP_PORT + i, False, cls.swap_clients[i])
                cls.http_threads.append(t)
                t.start()

            # Set future block rewards to nowhere (a random address), so wallet amounts stay constant
            eckey = ECKey()
            eckey.generate()
            void_block_rewards_pubkey = eckey.get_pubkey().get_bytes()

            cls.btc_addr = callnoderpc(0, 'getnewaddress', ['mining_addr', 'bech32'], base_rpc_port=BTC_BASE_RPC_PORT)
            num_blocks = 400  # Mine enough to activate segwit
            logging.info('Mining %d Bitcoin blocks to %s', num_blocks, cls.btc_addr)
            callnoderpc(0, 'generatetoaddress', [num_blocks, cls.btc_addr], base_rpc_port=BTC_BASE_RPC_PORT)

            # Switch addresses so wallet amounts stay constant
            num_blocks = 100
            cls.btc_addr = cls.swap_clients[0].ci(Coins.BTC).pubkey_to_segwit_address(void_block_rewards_pubkey)
            logging.info('Mining %d Bitcoin blocks to %s', num_blocks, cls.btc_addr)
            callnoderpc(0, 'generatetoaddress', [num_blocks, cls.btc_addr], base_rpc_port=BTC_BASE_RPC_PORT)

            checkForks(callnoderpc(0, 'getblockchaininfo', base_rpc_port=BTC_BASE_RPC_PORT))

            if cls.start_ltc_nodes:
                num_blocks = 400
                cls.ltc_addr = callnoderpc(0, 'getnewaddress', ['mining_addr', 'bech32'], base_rpc_port=LTC_BASE_RPC_PORT)
                logging.info('Mining %d Litecoin blocks to %s', num_blocks, cls.ltc_addr)
                callnoderpc(0, 'generatetoaddress', [num_blocks, cls.ltc_addr], base_rpc_port=LTC_BASE_RPC_PORT)

                num_blocks = 31
                cls.ltc_addr = cls.swap_clients[0].ci(Coins.LTC).pubkey_to_address(void_block_rewards_pubkey)
                logging.info('Mining %d Litecoin blocks to %s', num_blocks, cls.ltc_addr)
                callnoderpc(0, 'generatetoaddress', [num_blocks, cls.ltc_addr], base_rpc_port=LTC_BASE_RPC_PORT)

                # https://github.com/litecoin-project/litecoin/issues/807
                # Block 432 is when MWEB activates. It requires a peg-in. You'll need to generate an mweb address and send some coins to it. Then it will allow you to mine the next block.
                mweb_addr = callnoderpc(2, 'getnewaddress', ['mweb_addr', 'mweb'], base_rpc_port=LTC_BASE_RPC_PORT)
                callnoderpc(0, 'sendtoaddress', [mweb_addr, 1], base_rpc_port=LTC_BASE_RPC_PORT)

                num_blocks = 69
                cls.ltc_addr = cls.swap_clients[0].ci(Coins.LTC).pubkey_to_address(void_block_rewards_pubkey)
                callnoderpc(0, 'generatetoaddress', [num_blocks, cls.ltc_addr], base_rpc_port=LTC_BASE_RPC_PORT)

                checkForks(callnoderpc(0, 'getblockchaininfo', base_rpc_port=LTC_BASE_RPC_PORT))

            if cls.start_pivx_nodes:
                num_blocks = 400
                cls.pivx_addr = callnoderpc(0, 'getnewaddress', ['mining_addr'], base_rpc_port=PIVX_BASE_RPC_PORT)
                logging.info('Mining %d PIVX blocks to %s', num_blocks, cls.pivx_addr)
                callnoderpc(0, 'generatetoaddress', [num_blocks, cls.pivx_addr], base_rpc_port=PIVX_BASE_RPC_PORT)

                # Switch addresses so wallet amounts stay constant
                num_blocks = 100
                cls.pivx_addr = cls.swap_clients[0].ci(Coins.PIVX).pubkey_to_address(void_block_rewards_pubkey)
                logging.info('Mining %d PIVX blocks to %s', num_blocks, cls.pivx_addr)
                callnoderpc(0, 'generatetoaddress', [num_blocks, cls.pivx_addr], base_rpc_port=PIVX_BASE_RPC_PORT)

            num_blocks = 100
            if cls.start_xmr_nodes:
                cls.xmr_addr = cls.callxmrnodewallet(cls, 1, 'get_address')['address']
                if callrpc_xmr_na(XMR_BASE_RPC_PORT + 1, 'get_block_count')['count'] < num_blocks:
                    logging.info('Mining %d Monero blocks to %s.', num_blocks, cls.xmr_addr)
                    callrpc_xmr_na(XMR_BASE_RPC_PORT + 1, 'generateblocks', {'wallet_address': cls.xmr_addr, 'amount_of_blocks': num_blocks})
                logging.info('XMR blocks: %d', callrpc_xmr_na(XMR_BASE_RPC_PORT + 1, 'get_block_count')['count'])

            logging.info('Adding anon outputs')
            outputs = []
            for i in range(8):
                sx_addr = callnoderpc(1, 'getnewstealthaddress')
                outputs.append({'address': sx_addr, 'amount': 0.5})
            for i in range(6):
                callnoderpc(0, 'sendtypeto', ['part', 'anon', outputs])

            logging.info('Starting update thread.')
            signal.signal(signal.SIGINT, signal_handler)
            cls.update_thread = threading.Thread(target=run_loop, args=(cls,))
            cls.update_thread.start()

            pause_event.set()
            cls.coins_update_thread = threading.Thread(target=run_coins_loop, args=(cls,))
            cls.coins_update_thread.start()

        except Exception:
            traceback.print_exc()
            Test.tearDownClass()
            raise ValueError('setUpClass() failed.')

    @classmethod
    def tearDownClass(cls):
        logging.info('Finalising')
        test_delay_event.set()
        if cls.update_thread is not None:
            try:
                cls.update_thread.join()
            except Exception:
                logging.info('Failed to join update_thread')
        if cls.coins_update_thread is not None:
            try:
                cls.coins_update_thread.join()
            except Exception:
                logging.info('Failed to join coins_update_thread')

        for t in cls.http_threads:
            t.stop()
            t.join()
        for c in cls.swap_clients:
            c.finalise()
            c.fp.close()

        stopDaemons(cls.xmr_daemons)
        stopDaemons(cls.part_daemons)
        stopDaemons(cls.btc_daemons)
        stopDaemons(cls.ltc_daemons)
        stopDaemons(cls.pivx_daemons)

        super(BaseTest, cls).tearDownClass()

    def callxmrnodewallet(self, node_id, method, params=None):
        return callrpc_xmr(XMR_BASE_WALLET_RPC_PORT + node_id, self.xmr_wallet_auth[node_id], method, params)


class Test(BaseTest):
    __test__ = True

    def notest_00_delay(self):
        test_delay_event.wait(100000)

    def test_01_part_xmr(self):
        logging.info('---------- Test PART to XMR')
        swap_clients = self.swap_clients

        js_1 = read_json_api(1801, 'wallets')
        assert (make_int(js_1[Coins.XMR.name]['balance'], scale=12) > 0)
        assert (make_int(js_1[Coins.XMR.name]['unconfirmed'], scale=12) > 0)

        offer_id = swap_clients[0].postOffer(Coins.PART, Coins.XMR, 100 * COIN, 0.11 * XMR_COIN, 100 * COIN, SwapTypes.XMR_SWAP)
        wait_for_offer(test_delay_event, swap_clients[1], offer_id)
        offers = swap_clients[1].listOffers(filters={'offer_id': offer_id})
        assert (len(offers) == 1)
        offer = offers[0]

        bid_id = swap_clients[1].postXmrBid(offer_id, offer.amount_from)

        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.BID_RECEIVED)

        bid, xmr_swap = swap_clients[0].getXmrBid(bid_id)
        assert (xmr_swap)

        swap_clients[0].acceptXmrBid(bid_id)

        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.SWAP_COMPLETED, wait_for=180)
        wait_for_bid(test_delay_event, swap_clients[1], bid_id, BidStates.SWAP_COMPLETED, sent=True)

        js_0_end = read_json_api(1800, 'wallets')
        end_xmr = float(js_0_end['XMR']['balance']) + float(js_0_end['XMR']['unconfirmed'])
        assert (end_xmr > 10.9 and end_xmr < 11.0)

        bid_id_hex = bid_id.hex()
        path = f'bids/{bid_id_hex}/states'
        offerer_states = read_json_api(1800, path)
        bidder_states = read_json_api(1801, path)

        assert (compare_bid_states(offerer_states, self.states_offerer[0]) is True)
        assert (compare_bid_states(bidder_states, self.states_bidder[0]) is True)

    def test_011_smsgaddresses(self):
        logging.info('---------- Test address management and private offers')
        swap_clients = self.swap_clients
        js_1 = read_json_api(1801, 'smsgaddresses')

        post_json = {
            'addressnote': 'testing',
        }
        json_rv = json.loads(post_json_req('http://127.0.0.1:1801/json/smsgaddresses/new', post_json))
        new_address = json_rv['new_address']
        new_address_pk = json_rv['pubkey']

        js_2 = read_json_api(1801, 'smsgaddresses')
        assert (len(js_2) == len(js_1) + 1)
        found = False
        for addr in js_2:
            if addr['addr'] == new_address:
                assert (addr['note'] == 'testing')
                found = True
        assert (found is True)

        found = False
        lks = callnoderpc(1, 'smsglocalkeys')
        for key in lks['wallet_keys']:
            if key['address'] == new_address:
                assert (key['receive'] == '1')
                found = True
        assert (found is True)

        # Disable
        post_json = {
            'address': new_address,
            'addressnote': 'testing2',
            'active_ind': '0',
        }
        json_rv = json.loads(post_json_req('http://127.0.0.1:1801/json/smsgaddresses/edit', post_json))
        assert (json_rv['edited_address'] == new_address)

        js_3 = read_json_api(1801, 'smsgaddresses')
        found = False
        for addr in js_3:
            if addr['addr'] == new_address:
                assert (addr['note'] == 'testing2')
                assert (addr['active_ind'] == 0)
                found = True
        assert (found is True)

        found = False
        lks = callnoderpc(1, 'smsglocalkeys')
        for key in lks['wallet_keys']:
            if key['address'] == new_address:
                found = True
        assert (found is False)

        # Re-enable
        post_json = {
            'address': new_address,
            'active_ind': '1',
        }
        json_rv = json.loads(post_json_req('http://127.0.0.1:1801/json/smsgaddresses/edit', post_json))
        assert (json_rv['edited_address'] == new_address)

        found = False
        lks = callnoderpc(1, 'smsglocalkeys')
        for key in lks['wallet_keys']:
            if key['address'] == new_address:
                assert (key['receive'] == '1')
                found = True
        assert (found is True)

        post_json = {
            'addresspubkey': new_address_pk,
            'addressnote': 'testing_add_addr',
        }
        json_rv = json.loads(post_json_req('http://127.0.0.1:1800/json/smsgaddresses/add', post_json))
        assert (json_rv['added_address'] == new_address)

        post_json = {
            'addr_to': new_address,
            'addr_from': -1,
            'coin_from': 1,
            'coin_to': 6,
            'amt_from': 1,
            'amt_to': 1,
            'lockhrs': 24,
            'autoaccept': True}
        rv = json.loads(post_json_req('http://127.0.0.1:1800/json/offers/new', post_json))
        offer_id_hex = rv['offer_id']

        wait_for_offer(test_delay_event, swap_clients[1], bytes.fromhex(offer_id_hex))

        rv = read_json_api(1801, f'offers/{offer_id_hex}')
        assert (rv[0]['addr_to'] == new_address)

        rv = read_json_api(1800, f'offers/{offer_id_hex}')
        assert (rv[0]['addr_to'] == new_address)

    def test_02_leader_recover_a_lock_tx(self):
        logging.info('---------- Test PART to XMR leader recovers coin a lock tx')
        swap_clients = self.swap_clients

        offer_id = swap_clients[0].postOffer(
            Coins.PART, Coins.XMR, 101 * COIN, 0.12 * XMR_COIN, 101 * COIN, SwapTypes.XMR_SWAP,
            lock_type=TxLockTypes.SEQUENCE_LOCK_BLOCKS, lock_value=12)
        wait_for_offer(test_delay_event, swap_clients[1], offer_id)
        offer = swap_clients[1].getOffer(offer_id)

        bid_id = swap_clients[1].postXmrBid(offer_id, offer.amount_from)

        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.BID_RECEIVED)

        bid, xmr_swap = swap_clients[0].getXmrBid(bid_id)
        assert (xmr_swap)

        swap_clients[1].setBidDebugInd(bid_id, DebugTypes.BID_STOP_AFTER_COIN_A_LOCK)

        swap_clients[0].acceptXmrBid(bid_id)

        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.XMR_SWAP_FAILED_REFUNDED, wait_for=180)
        wait_for_bid(test_delay_event, swap_clients[1], bid_id, [BidStates.BID_STALLED_FOR_TEST, BidStates.XMR_SWAP_FAILED], sent=True)

        bid_id_hex = bid_id.hex()
        path = f'bids/{bid_id_hex}/states'
        offerer_states = read_json_api(1800, path)

        assert (compare_bid_states(offerer_states, self.states_offerer[1]) is True)

    def test_03_follower_recover_a_lock_tx(self):
        logging.info('---------- Test PART to XMR follower recovers coin a lock tx')
        swap_clients = self.swap_clients

        offer_id = swap_clients[0].postOffer(
            Coins.PART, Coins.XMR, 101 * COIN, 0.13 * XMR_COIN, 101 * COIN, SwapTypes.XMR_SWAP,
            lock_type=TxLockTypes.SEQUENCE_LOCK_BLOCKS, lock_value=12)
        wait_for_offer(test_delay_event, swap_clients[1], offer_id)
        offer = swap_clients[1].getOffer(offer_id)

        bid_id = swap_clients[1].postXmrBid(offer_id, offer.amount_from)

        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.BID_RECEIVED)

        bid, xmr_swap = swap_clients[0].getXmrBid(bid_id)
        assert (xmr_swap)

        swap_clients[1].setBidDebugInd(bid_id, DebugTypes.BID_STOP_AFTER_COIN_A_LOCK)
        swap_clients[0].setBidDebugInd(bid_id, DebugTypes.BID_DONT_SPEND_COIN_A_LOCK_REFUND)

        swap_clients[0].acceptXmrBid(bid_id)

        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.BID_STALLED_FOR_TEST, wait_for=180)
        wait_for_bid(test_delay_event, swap_clients[1], bid_id, BidStates.XMR_SWAP_FAILED_SWIPED, wait_for=80, sent=True)

        wait_for_none_active(test_delay_event, 1800)
        wait_for_none_active(test_delay_event, 1801)

        bid_id_hex = bid_id.hex()
        path = f'bids/{bid_id_hex}/states'
        bidder_states = read_json_api(1801, path)

        bidder_states = [s for s in bidder_states if s[1] != 'Bid Stalled (debug)']
        assert (compare_bid_states(bidder_states, self.states_bidder[2]) is True)

    def test_04_follower_recover_b_lock_tx(self):
        logging.info('---------- Test PART to XMR follower recovers coin b lock tx')

        swap_clients = self.swap_clients

        offer_id = swap_clients[0].postOffer(
            Coins.PART, Coins.XMR, 101 * COIN, 0.14 * XMR_COIN, 101 * COIN, SwapTypes.XMR_SWAP,
            lock_type=TxLockTypes.SEQUENCE_LOCK_BLOCKS, lock_value=28)
        wait_for_offer(test_delay_event, swap_clients[1], offer_id)
        offer = swap_clients[1].getOffer(offer_id)

        bid_id = swap_clients[1].postXmrBid(offer_id, offer.amount_from)
        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.BID_RECEIVED)

        bid, xmr_swap = swap_clients[0].getXmrBid(bid_id)
        assert (xmr_swap)

        swap_clients[1].setBidDebugInd(bid_id, DebugTypes.CREATE_INVALID_COIN_B_LOCK)

        swap_clients[0].acceptXmrBid(bid_id)

        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.XMR_SWAP_FAILED_REFUNDED, wait_for=180)
        wait_for_bid(test_delay_event, swap_clients[1], bid_id, BidStates.XMR_SWAP_FAILED_REFUNDED, sent=True)

        bid_id_hex = bid_id.hex()
        path = f'bids/{bid_id_hex}/states'
        offerer_states = read_json_api(1800, path)
        bidder_states = read_json_api(1801, path)

        assert (compare_bid_states(offerer_states, self.states_offerer[1]) is True)
        assert (compare_bid_states(bidder_states, self.states_bidder[1]) is True)

    def test_05_btc_xmr(self):
        logging.info('---------- Test BTC to XMR')
        swap_clients = self.swap_clients
        offer_id = swap_clients[0].postOffer(Coins.BTC, Coins.XMR, 10 * COIN, 100 * XMR_COIN, 10 * COIN, SwapTypes.XMR_SWAP)
        wait_for_offer(test_delay_event, swap_clients[1], offer_id)
        offers = swap_clients[1].listOffers(filters={'offer_id': offer_id})
        offer = offers[0]

        swap_clients[1].ci(Coins.XMR).setFeePriority(3)

        bid_id = swap_clients[1].postXmrBid(offer_id, offer.amount_from)

        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.BID_RECEIVED)

        bid, xmr_swap = swap_clients[0].getXmrBid(bid_id)
        assert (xmr_swap)

        swap_clients[0].acceptXmrBid(bid_id)

        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.SWAP_COMPLETED, wait_for=180)
        wait_for_bid(test_delay_event, swap_clients[1], bid_id, BidStates.SWAP_COMPLETED, sent=True)

        swap_clients[1].ci(Coins.XMR).setFeePriority(0)

    def test_06_multiple_swaps(self):
        logging.info('---------- Test Multiple concurrent swaps')
        swap_clients = self.swap_clients

        js_w0_before = read_json_api(1800, 'wallets')
        js_w1_before = read_json_api(1801, 'wallets')

        amt_1 = make_int(random.uniform(0.001, 49.0), scale=8, r=1)
        amt_2 = make_int(random.uniform(0.001, 49.0), scale=8, r=1)

        rate_1 = make_int(random.uniform(80.0, 110.0), scale=12, r=1)
        rate_2 = make_int(random.uniform(0.01, 0.5), scale=12, r=1)

        logging.info('amt_1 {}, rate_1 {}'.format(amt_1, rate_1))
        logging.info('amt_2 {}, rate_2 {}'.format(amt_2, rate_2))
        offer1_id = swap_clients[0].postOffer(Coins.BTC, Coins.XMR, amt_1, rate_1, amt_1, SwapTypes.XMR_SWAP)
        offer2_id = swap_clients[0].postOffer(Coins.PART, Coins.XMR, amt_2, rate_2, amt_2, SwapTypes.XMR_SWAP)

        wait_for_offer(test_delay_event, swap_clients[1], offer1_id)
        offer1 = swap_clients[1].getOffer(offer1_id)
        wait_for_offer(test_delay_event, swap_clients[1], offer2_id)
        offer2 = swap_clients[1].getOffer(offer2_id)

        bid1_id = swap_clients[1].postXmrBid(offer1_id, offer1.amount_from)
        bid2_id = swap_clients[1].postXmrBid(offer2_id, offer2.amount_from)

        offer3_id = swap_clients[0].postOffer(Coins.PART, Coins.XMR, 11 * COIN, 0.15 * XMR_COIN, 11 * COIN, SwapTypes.XMR_SWAP)

        wait_for_bid(test_delay_event, swap_clients[0], bid1_id, BidStates.BID_RECEIVED)
        swap_clients[0].acceptXmrBid(bid1_id)

        wait_for_offer(test_delay_event, swap_clients[1], offer3_id)
        offer3 = swap_clients[1].getOffer(offer3_id)
        bid3_id = swap_clients[1].postXmrBid(offer3_id, offer3.amount_from)

        wait_for_bid(test_delay_event, swap_clients[0], bid2_id, BidStates.BID_RECEIVED)
        swap_clients[0].acceptXmrBid(bid2_id)

        wait_for_bid(test_delay_event, swap_clients[0], bid3_id, BidStates.BID_RECEIVED)
        swap_clients[0].acceptXmrBid(bid3_id)

        wait_for_bid(test_delay_event, swap_clients[0], bid1_id, BidStates.SWAP_COMPLETED, wait_for=180)
        wait_for_bid(test_delay_event, swap_clients[1], bid1_id, BidStates.SWAP_COMPLETED, sent=True)

        wait_for_bid(test_delay_event, swap_clients[0], bid2_id, BidStates.SWAP_COMPLETED, wait_for=120)
        wait_for_bid(test_delay_event, swap_clients[1], bid2_id, BidStates.SWAP_COMPLETED, sent=True)

        wait_for_bid(test_delay_event, swap_clients[0], bid3_id, BidStates.SWAP_COMPLETED, wait_for=120)
        wait_for_bid(test_delay_event, swap_clients[1], bid3_id, BidStates.SWAP_COMPLETED, sent=True)

        wait_for_none_active(test_delay_event, 1800)
        wait_for_none_active(test_delay_event, 1801)

        js_w0_after = read_json_api(1800, 'wallets')
        js_w1_after = read_json_api(1801, 'wallets')
        assert (make_int(js_w1_after['BTC']['balance'], scale=8, r=1) - (make_int(js_w1_before['BTC']['balance'], scale=8, r=1) + amt_1) < 1000)

    def test_07_revoke_offer(self):
        logging.info('---------- Test offer revocaction')
        swap_clients = self.swap_clients
        offer_id = swap_clients[0].postOffer(Coins.BTC, Coins.XMR, 10 * COIN, 100 * XMR_COIN, 10 * COIN, SwapTypes.XMR_SWAP)
        wait_for_offer(test_delay_event, swap_clients[1], offer_id)

        swap_clients[0].revokeOffer(offer_id)

        wait_for_no_offer(test_delay_event, swap_clients[1], offer_id)

    def test_08_withdraw(self):
        logging.info('---------- Test XMR withdrawals')
        swap_clients = self.swap_clients
        js_0 = read_json_api(1800, 'wallets')
        address_to = js_0[Coins.XMR.name]['deposit_address']

        js_1 = read_json_api(1801, 'wallets')
        assert (float(js_1[Coins.XMR.name]['balance']) > 0.0)

        swap_clients[1].withdrawCoin(Coins.XMR, 1.1, address_to, False)

    def test_09_auto_accept(self):
        logging.info('---------- Test BTC to XMR auto accept')
        swap_clients = self.swap_clients
        amt_swap = make_int(random.uniform(0.01, 11.0), scale=8, r=1)
        rate_swap = make_int(random.uniform(10.0, 101.0), scale=12, r=1)
        offer_id = swap_clients[0].postOffer(Coins.BTC, Coins.XMR, amt_swap, rate_swap, amt_swap, SwapTypes.XMR_SWAP, auto_accept_bids=True)
        wait_for_offer(test_delay_event, swap_clients[1], offer_id)
        offer = swap_clients[1].listOffers(filters={'offer_id': offer_id})[0]

        bid_id = swap_clients[1].postXmrBid(offer_id, offer.amount_from)
        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.SWAP_COMPLETED, wait_for=180)
        wait_for_bid(test_delay_event, swap_clients[1], bid_id, BidStates.SWAP_COMPLETED, sent=True)

    def test_09_1_auto_accept_multiple(self):
        logging.info('---------- Test BTC to XMR auto accept multiple bids')
        swap_clients = self.swap_clients
        amt_swap = make_int(10, scale=8, r=1)
        rate_swap = make_int(100, scale=12, r=1)
        min_bid = make_int(1, scale=8, r=1)

        extra_options = {
            'amount_negotiable': True,
            'automation_id': 1,
        }
        offer_id = swap_clients[0].postOffer(Coins.BTC, Coins.XMR, amt_swap, rate_swap, min_bid, SwapTypes.XMR_SWAP, extra_options=extra_options)
        wait_for_offer(test_delay_event, swap_clients[1], offer_id)
        offer = swap_clients[1].listOffers(filters={'offer_id': offer_id})[0]

        below_min_bid = min_bid - 1

        # Ensure bids below the minimum amount fails on sender and recipient.
        try:
            bid_id = swap_clients[1].postBid(offer_id, below_min_bid)
        except Exception as e:
            assert ('Bid amount below minimum' in str(e))
        extra_bid_options = {
            'debug_skip_validation': True,
        }
        bid_id = swap_clients[1].postBid(offer_id, below_min_bid, extra_options=extra_bid_options)

        events = wait_for_event(test_delay_event, swap_clients[0], Concepts.NETWORK_MESSAGE, bid_id)
        assert ('Bid amount below minimum' in events[0].event_msg)

        bid_ids = []
        for i in range(5):
            bid_ids.append(swap_clients[1].postBid(offer_id, min_bid))

        # Should fail > max concurrent
        test_delay_event.wait(1.0)
        bid_id = swap_clients[1].postBid(offer_id, min_bid)
        events = wait_for_event(test_delay_event, swap_clients[0], Concepts.AUTOMATION, bid_id)
        assert ('Already have 5 bids to complete' in events[0].event_msg)

        for bid_id in bid_ids:
            wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.SWAP_COMPLETED, wait_for=180)
            wait_for_bid(test_delay_event, swap_clients[1], bid_id, BidStates.SWAP_COMPLETED, sent=True)

        amt_bid = make_int(5, scale=8, r=1)

        # Should fail > total value
        amt_bid += 1
        bid_id = swap_clients[1].postBid(offer_id, amt_bid)
        events = wait_for_event(test_delay_event, swap_clients[0], Concepts.AUTOMATION, bid_id)
        assert ('Over remaining offer value' in events[0].event_msg)

        # Should pass
        amt_bid -= 1
        bid_id = swap_clients[1].postBid(offer_id, amt_bid)
        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.SWAP_COMPLETED, wait_for=180)
        wait_for_bid(test_delay_event, swap_clients[1], bid_id, BidStates.SWAP_COMPLETED, sent=True)

    def test_10_locked_refundtx(self):
        logging.info('---------- Test Refund tx is locked')
        swap_clients = self.swap_clients
        offer_id = swap_clients[0].postOffer(Coins.BTC, Coins.XMR, 10 * COIN, 100 * XMR_COIN, 10 * COIN, SwapTypes.XMR_SWAP)
        wait_for_offer(test_delay_event, swap_clients[1], offer_id)
        offers = swap_clients[1].listOffers(filters={'offer_id': offer_id})
        offer = offers[0]

        bid_id = swap_clients[1].postXmrBid(offer_id, offer.amount_from)

        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.BID_RECEIVED)

        bid, xmr_swap = swap_clients[0].getXmrBid(bid_id)
        assert (xmr_swap)

        swap_clients[1].setBidDebugInd(bid_id, DebugTypes.BID_STOP_AFTER_COIN_A_LOCK)

        swap_clients[0].acceptXmrBid(bid_id)

        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.XMR_SWAP_SCRIPT_COIN_LOCKED, wait_for=180)

        bid, xmr_swap = swap_clients[0].getXmrBid(bid_id)
        assert (xmr_swap)

        try:
            swap_clients[0].ci(Coins.BTC).publishTx(xmr_swap.a_lock_refund_tx)
            assert (False), 'Lock refund tx should be locked'
        except Exception as e:
            assert ('non-BIP68-final' in str(e))

    def test_11_particl_anon(self):
        logging.info('---------- Test Particl anon transactions')
        swap_clients = self.swap_clients

        js_0 = read_json_api(1800, 'wallets/part')
        assert (float(js_0['anon_balance']) == 0.0)
        node0_anon_before = js_0['anon_balance'] + js_0['anon_pending']

        wait_for_balance(test_delay_event, 'http://127.0.0.1:1801/json/wallets/part', 'balance', 200.0)
        js_1 = read_json_api(1801, 'wallets/part')
        assert (float(js_1['balance']) > 200.0)
        node1_anon_before = js_1['anon_balance'] + js_1['anon_pending']

        callnoderpc(1, 'reservebalance', [True, 1000000])  # Stop staking to avoid conflicts (input used by tx->anon staked before tx gets in the chain)
        post_json = {
            'value': 100,
            'address': js_1['stealth_address'],
            'subfee': False,
            'type_to': 'anon',
        }
        json_rv = json.loads(post_json_req('http://127.0.0.1:1801/json/wallets/part/withdraw', post_json))
        assert (len(json_rv['txid']) == 64)

        logging.info('Waiting for anon balance')
        wait_for_balance(test_delay_event, 'http://127.0.0.1:1801/json/wallets/part', 'anon_balance', 100.0 + node1_anon_before)
        js_1 = read_json_api(1801, 'wallets/part')
        node1_anon_before = js_1['anon_balance'] + js_1['anon_pending']

        callnoderpc(1, 'reservebalance', [False])
        post_json = {
            'value': 10,
            'address': js_0['stealth_address'],
            'subfee': True,
            'type_from': 'anon',
            'type_to': 'blind',
        }
        json_rv = json.loads(post_json_req('http://127.0.0.1:1801/json/wallets/part/withdraw', post_json))
        assert (len(json_rv['txid']) == 64)

        logging.info('Waiting for blind balance')
        wait_for_balance(test_delay_event, 'http://127.0.0.1:1800/json/wallets/part', 'blind_balance', 9.8)
        if float(js_0['blind_balance']) >= 10.0:
            raise ValueError('Expect blind balance < 10')

        amt_swap = make_int(random.uniform(0.1, 2.0), scale=8, r=1)
        rate_swap = make_int(random.uniform(2.0, 20.0), scale=8, r=1)
        offer_id = swap_clients[0].postOffer(Coins.BTC, Coins.PART_ANON, amt_swap, rate_swap, amt_swap, SwapTypes.XMR_SWAP)
        wait_for_offer(test_delay_event, swap_clients[1], offer_id)
        offers = swap_clients[0].listOffers(filters={'offer_id': offer_id})
        offer = offers[0]

        bid_id = swap_clients[1].postXmrBid(offer_id, offer.amount_from)

        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.BID_RECEIVED)

        bid, xmr_swap = swap_clients[0].getXmrBid(bid_id)
        assert (xmr_swap)
        amount_to = float(format_amount(bid.amount_to, 8))

        swap_clients[0].acceptXmrBid(bid_id)

        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.SWAP_COMPLETED, wait_for=180)
        wait_for_bid(test_delay_event, swap_clients[1], bid_id, BidStates.SWAP_COMPLETED, sent=True)

        js_1 = read_json_api(1801, 'wallets/part')
        assert (js_1['anon_balance'] < node1_anon_before - amount_to)

        js_0 = read_json_api(1800, 'wallets/part')
        assert (js_0['anon_balance'] + js_0['anon_pending'] > node0_anon_before + (amount_to - 0.05))

    def test_12_particl_blind(self):
        logging.info('---------- Test Particl blind transactions')
        swap_clients = self.swap_clients

        js_0 = read_json_api(1800, 'wallets/part')
        node0_blind_before = js_0['blind_balance'] + js_0['blind_unconfirmed']

        wait_for_balance(test_delay_event, 'http://127.0.0.1:1801/json/wallets/part', 'balance', 200.0)
        js_1 = read_json_api(1801, 'wallets/part')
        assert (float(js_1['balance']) > 200.0)
        node1_blind_before = js_1['blind_balance'] + js_1['blind_unconfirmed']

        post_json = {
            'value': 100,
            'address': js_0['stealth_address'],
            'subfee': False,
            'type_to': 'blind',
        }
        json_rv = json.loads(post_json_req('http://127.0.0.1:1800/json/wallets/part/withdraw', post_json))
        assert (len(json_rv['txid']) == 64)

        logging.info('Waiting for blind balance')
        wait_for_balance(test_delay_event, 'http://127.0.0.1:1800/json/wallets/part', 'blind_balance', 100.0 + node0_blind_before)
        js_0 = read_json_api(1800, 'wallets/part')
        node0_blind_before = js_0['blind_balance'] + js_0['blind_unconfirmed']

        amt_swap = make_int(random.uniform(0.1, 2.0), scale=8, r=1)
        rate_swap = make_int(random.uniform(2.0, 20.0), scale=8, r=1)
        offer_id = swap_clients[0].postOffer(Coins.PART_BLIND, Coins.XMR, amt_swap, rate_swap, amt_swap, SwapTypes.XMR_SWAP)
        wait_for_offer(test_delay_event, swap_clients[1], offer_id)
        offers = swap_clients[0].listOffers(filters={'offer_id': offer_id})
        offer = offers[0]

        bid_id = swap_clients[1].postXmrBid(offer_id, offer.amount_from)

        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.BID_RECEIVED)

        swap_clients[0].acceptXmrBid(bid_id)

        wait_for_bid(test_delay_event, swap_clients[0], bid_id, BidStates.SWAP_COMPLETED, wait_for=180)
        wait_for_bid(test_delay_event, swap_clients[1], bid_id, BidStates.SWAP_COMPLETED, sent=True)

        amount_from = float(format_amount(amt_swap, 8))
        js_1 = read_json_api(1801, 'wallets/part')
        node1_blind_after = js_1['blind_balance'] + js_1['blind_unconfirmed']
        assert (node1_blind_after > node1_blind_before + (amount_from - 0.05))

        js_0 = read_json_api(1800, 'wallets/part')
        node0_blind_after = js_0['blind_balance'] + js_0['blind_unconfirmed']
        assert (node0_blind_after < node0_blind_before - amount_from)

    def test_98_withdraw_all(self):
        logging.info('---------- Test XMR withdrawal all')
        try:
            logging.info('Disabling XMR mining')
            pause_event.clear()

            js_0 = read_json_api(1800, 'wallets')
            address_to = js_0[Coins.XMR.name]['deposit_address']

            wallets1 = read_json_api(TEST_HTTP_PORT + 1, 'wallets')
            xmr_total = float(wallets1[Coins.XMR.name]['balance'])
            assert (xmr_total > 10)

            post_json = {
                'value': 10,
                'address': address_to,
                'subfee': True,
            }
            json_rv = json.loads(post_json_req('http://127.0.0.1:{}/json/wallets/xmr/withdraw'.format(TEST_HTTP_PORT + 1), post_json))
            assert (json_rv['error'] == 'Withdraw value must be close to total to use subfee/sweep_all.')

            post_json['value'] = xmr_total
            json_rv = json.loads(post_json_req('http://127.0.0.1:{}/json/wallets/xmr/withdraw'.format(TEST_HTTP_PORT + 1), post_json))
            assert (len(json_rv['txid']) == 64)
        finally:
            logging.info('Restoring XMR mining')
            pause_event.set()


if __name__ == '__main__':
    unittest.main()
