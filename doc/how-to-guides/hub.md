```console
$ openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:secp384r1 -days 3650 -nodes -keyout not-my-board-root-ca.key -out not-my-board-root-ca.crt -subj "/CN=not-my-board-root-ca"
$ openssl req -x509 -CA not-my-board-root-ca.crt -CAkey not-my-board-root-ca.key -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -days 3650 -nodes -keyout not-my-board.key -out not-my-board.crt -subj "/CN=not-my-board.io" -addext "subjectAltName=DNS:not-my-board.io,DNS:*.not-my-board.io,IP:30.30.4.176" -addext "basicConstraints=CA:FALSE" -addext "keyUsage=digitalSignature,keyEncipherment" -addext "extendedKeyUsage=serverAuth"
```
