project('not-my-board', version: '0.1.0')

py = import('python').find_installation()

py.install_sources(
  'not_my_board/__init__.py',
  'not_my_board/__main__.py',
  'not_my_board/_agent.py',
  'not_my_board/_client.py',
  'not_my_board/_export.py',
  'not_my_board/_http.py',
  'not_my_board/_jsonrpc.py',
  'not_my_board/_models.py',
  'not_my_board/_serve.py',
  'not_my_board/_usbip.py',
  subdir: 'not_my_board',
)

py.install_sources(
  'not_my_board/_util/__init__.py',
  'not_my_board/_util/_asyncio.py',
  'not_my_board/_util/_matching.py',
  'not_my_board/_util/_misc.py',
  subdir: 'not_my_board/_util',
)

py.install_sources(
  'not_my_board/cli/__init__.py',
  subdir: 'not_my_board/cli',
)

configure_file(
  input: 'not_my_board/__about__.py.in',
  output: '__about__.py',
  configuration: {
    'VERSION': meson.project_version(),
  },
  install_dir: py.get_install_dir() / 'not_my_board',
)
