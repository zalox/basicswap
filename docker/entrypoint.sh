#!/usr/bin/env bash
set -e

chown -R swap_user "$DATADIRS"

if [ "$WITH_COINS" != "" ] \
     && [ "$CURRENT_XMR_HEIGHT" != "" ] \
     && ! [ -d /coindata/particl ]
then
  gosu swap_user basicswap-prepare \
       --datadir=/coindata \
       --withcoins=$WITH_COINS \
       --htmlhost="0.0.0.0" \
       --wshost="0.0.0.0" \
       --xmrrestoreheight=$CURRENT_XMR_HEIGHT
fi

gosu swap_user "$@"
