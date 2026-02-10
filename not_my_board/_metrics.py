import prometheus_client

prometheus_client.disable_created_metrics()


PLACES_REGISTERED = prometheus_client.Gauge(
    "not_my_board_places_registered",
    "Number of registered places",
)
PLACES_RESERVED = prometheus_client.Gauge(
    "not_my_board_places_reserved",
    "Number of reserved places",
)
RESERVATION_QUEUE_LENGTH = prometheus_client.Gauge(
    "not_my_board_reservation_queue_length",
    "Number of pending reservation requests",
)


asgi_app = prometheus_client.make_asgi_app()
