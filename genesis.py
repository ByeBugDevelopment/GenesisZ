#!/usr/bin/env python3

# Copyright (C) 2016-2018 Sebastian Stammler
#
# This file is part of GenesisZ.
#
# It is subject to the license terms in the LICENSE file found in the top-level
# directory of this distribution.
#
# No part of GenesisZ, including this file, may be copied, modified,
# propagated, or distributed except according to the terms contained in the
# LICENSE file.

import argparse
import asyncio
import sys, os, time, re

from bitcoin.core import *
from bitcoin.core.script import CScript, OP_CHECKSIG
from zcash.core import *
import blockexplorer as be
from pyblake2 import blake2s

from logger import warn, fatal, verb
import logger
from solvers import SilentarmySolver, TrompSolver, SolverException

def main():
    args = parse_args()

    eh = build_EquihashInputHeader(args)
    if args.solver_type == 'tromp':
        solver = TrompSolver(args.solver, eh, args.rounds, args.nonce, args.threads)
    elif args.solver_type == 'silentarmy':
        solver = SilentarmySolver(args.solver, eh, args.rounds, args.nonce)

    # as if I cared about windows users...
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
    else:
        loop = asyncio.get_event_loop()

    try:
        solution, nonce = loop.run_until_complete(solver.run())
        h = CZBlockHeader.from_EquihashHeader(eh, solution, nonce)
        print('Solution found!\nHeader Hash: {}\nNonce: {}\n{}'
                .format(b2lx(h.GetHash()), b2lx(nonce), b2x(solution)))
    except SolverException as e:
        warn('{}\nExiting.'.format(e))
    finally:
        loop.close()

def parse_args():
    def lbytes32(s):
        """Converts a hex string into a 32 bytes long byte array, litte endian"""
        if len(s) > 32:
            warn('Nonce can be at most 32 bytes long, is {:d}! Will be truncated'
                    .format(len(s)))
            return lx(s[:64])
        return lx('0'*(64-len(s)) + s)
    def split(s):
        """Runs s.split()"""
        return s.split()
    def smartint(i):
        if i.startswith('0x'):
            return int(i, 16)
        else:
            return int(i, 10)

    parser = argparse.ArgumentParser(description="This script uses any Equihash solver to find a solution for the specified genesis block")
    parser.add_argument("-c", "--chainparams", dest="chain", default="mainnet",
            choices=["mainnet", "testnet", "regtest"],
            help="""Select the core chain parameters for PoW limit and parameters
            N and K.""")
    parser.add_argument("-t", "--time",
            dest="time", action="store", type=int, default=int(time.time()),
            help="unix time to set in block header (defaults to current time)")
    parser.add_argument("-C", "--coinname", dest="coinname", default="Zcash",
            help="the coin name prepends the blake2s hash of timestamp in pszTimestamp")
    parser.add_argument("-z", "--timestamp", dest="timestamp",
            default="The Economist 2016-10-29 Known unknown: Another crypto-currency is born. BTC#436254 0000000000000000044f321997f336d2908cf8c8d6893e88dbf067e2d949487d ETH#2521903 483039a6b6bd8bd05f0584f9a078d075e454925eb71c1f13eaff59b405a721bb DJIA close on 27 Oct 2016: 18,169.68",
            help="""the pszTimestamp found in the input coinbase transaction
            script. Will be blake2s'd and then prefixed by coin name. Default
            is Zcash's mainnet pszTimestamp. You may use tokens of the form
            {XYZ}, which will be replaced by the current block index and hash
            of coin XZY (BTC, ETH or ZEC). Always the latest block is retrieved,
            regardless of time argument.""")
    parser.add_argument("-Z", "--pszTimestamp", dest="pszTimestamp", default=None,
            help="Specify the pszTimestamp directly. Will ignore options -C and -z")
    parser.add_argument("-p", "--pubkey", dest="pubkey", type=x,
            default=x("04678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61deb649f6bc3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5f"),
            help="the pubkey found in the output transaction script")
    parser.add_argument("-b", "--bits", dest="bits", type=smartint,
            default=0x1f07ffff,
            help="the target in compact representation, defining a difficulty of 1")
    parser.add_argument("-E", "--extra-nonce", dest="extranonce", type=smartint,
            default=None,
            help="Usually, the coinbase script contains the nBits as fixed first"
            " data, which in bitcoin is also referred to as extra nonce. This"
            " conventional behaviour can be changed by specifying this parameter"
            " (not recommended for mainnet, useful for testnet).")
    parser.add_argument("-V", "--value", dest="value", default=0, type=int,
            help="output transaction value in zatoshi (1 ZEC = 100000000 zatoshi)")
    parser.add_argument("-n", "--nonce", dest="nonce", default=b'\x00'*32,
            type=lbytes32, help="nonce to start with when searching for a valid"
            " equihash solution; parsed as hex, leading zeros may be omitted.")
    parser.add_argument("-r", "--rounds", dest="rounds", default=1,
            type=int, help="how many nonces to check at most")
    parser.add_argument("-s", "--solver", dest="solver",
            type=split, default=split("../../equihash/eq14451"),
            help="""path to solver binary. Currently supported are silentarmy
            (sa-solver) and Tromp (equi/eq14451). Command line arguments may be
            passed, although that should be unnecessary.""")
    parser.add_argument("-S", "--solver-type", dest="solver_type",
            default="tromp",
            choices=["tromp", "silentarmy"],
            help="""Set the type of solver explicitly.
            Otherwise GenesisZ tries to infer the type from the binary name
            (equi* -> tromp, sa-solver -> silentarmy)""")
    parser.add_argument("-T", "--threads", dest="threads", default=1,
            type=int, help="How many CPU threads to use when solving with Tromp.")
    parser.add_argument("-v", "--verbose",
            dest="verbose", action="store_true",
            help="verbose output")

    args = parser.parse_args()
    logger.verbose = args.verbose
    SelectCoreParams(args.chain)
    # infer solver type from binary if not set
    if not args.solver_type:
        if args.solver[0].endswith('sa-solver'):
            args.solver_type = 'silentarmy'
        elif re.search(r'eq\w*$', args.solver[0]):
            args.solver_type = 'tromp'
        else:
            fatal("Couldn't infer solver type from binary name and type not set with -S")

    # silentarmy only supports N,K=200,9
    if args.solver_type == 'silentarmy' and args.chain == 'regtest':
        fatal("Silentarmy doesn't support regtest parameters N,K=48,5. Get Tromp's equihash solver.")

    verb('Chain: ' + args.chain)
    verb('Time: {:d}'.format(args.time))
    verb('Start Nonce: ' + b2lx(args.nonce))
    verb('Pubkey: ' + b2x(args.pubkey))
    verb('Solver: {}'.format(args.solver))
    verb('Solver type: ' + args.solver_type)

    return args

def build_EquihashInputHeader(args):
    pszTimestamp = args.pszTimestamp if args.pszTimestamp else \
            build_pszTimestamp(args.coinname, args.timestamp)
    verb("pszTimestamp: " + pszTimestamp)
    pk, bits = args.pubkey, args.bits
    extranonce = args.extranonce if args.extranonce else bits
    # Input transaction
    scriptSig = CScript() + extranonce + b'\x04' + pszTimestamp.encode('UTF-8')
    txin=CMutableTxIn(scriptSig=scriptSig)
    # Output transaction
    scriptPubKey = CScript() + pk + OP_CHECKSIG
    txout = CMutableTxOut(nValue = args.value, scriptPubKey = scriptPubKey)

    tx = CMutableTransaction(vin=[txin], vout=[txout])
    txhash = tx.GetTxid()
    verb("TX/merkle root hash: " + b2lx(txhash))

    return CEquihashHeader(nTime=args.time, nBits=bits,
        nNonce=args.nonce, hashMerkleRoot=txhash)

def build_pszTimestamp(coinname, timestamp):
    # Build the timestamp. First, replace all {XYZ}
    for coin in re.findall(r'\{[A-Z]{3}\}', timestamp):
            timestamp = timestamp.replace(coin, get_latest_block_str(coin[1:4]))
    verb("timestamp after substitution: " + timestamp)
    return coinname + \
            blake2s(timestamp.encode('UTF-8')).hexdigest()

def get_latest_block_str(coin):
    return '{}#{:d} {}'.format(coin, *be.get_latest(coin))

if __name__ == "__main__":
    main()
