# Sharing an HTTP Server

In this tutorial you will setup a board farm and share a simple HTTP server
through the board farm. You will do this all on a single host, so you can
interact with all the components easily and get a complete picture of the board
farm.

First, start the board farm hub. The hub listens on port `2092` by default.
Leave it running and continue in a new terminal window.
```console
$ not-my-board hub
```

Now start the simple HTTP server, that will be shared with the board farm. Use
the HTTP server from the Python standard library to serve a file named `hello`
and accept connections on port `8080`. Leave it running and continue in a new
terminal window.
```console
$ echo 'Hello, World!' > hello
$ python3 -m http.server 8080
```

Describe the place you want to export: It consists of a single part -- the HTTP
server:
```{code-block} toml
:caption: tutorial-tcp-place.toml

port = 2192

[[parts]]
compatible = [ "tutorial-http-server" ]
tcp.http = { host = "localhost", port = 8080 }
```

Now register the place in the board farm. Leave the exporter running and
continue in a new terminal window.
```console
$ not-my-board export http://localhost:2092 ./tutorial-tcp-place.toml
```

Before you can reserve and attach the exported place you need to start a
background process, that handles the communication with the board farm: the
agent. This usually runs on a different host than the exporter, but as mentioned
before, you run everything on a single host. Start the agent, leave it running
and continue in a new terminal window.
```console
$ not-my-board agent http://localhost:2092
```

Now create a file with the import description. It says "I want a place, which
has one part. This part has at least the tag `tutorial-http-server` and a TCP
interface named `http`. Bind that interface to localhost, port `8081`."
```{code-block} toml
:caption: tutorial-tcp.toml

[parts.http-server]
compatible = [ "tutorial-http-server" ]
tcp.http = { local_port = 8081 }
```

Use that import description to reserve and attach the place, that you previously
exported.
```console
$ not-my-board attach ./tutorial-tcp.toml
```

Now the exported HTTP server is available through the board farm. Notice that
the real HTTP server is listening on port `8080` and the agent exposes the
imported HTTP server on port `8081`:
```console
$ curl http://localhost:8081/hello
Hello, World!
```
