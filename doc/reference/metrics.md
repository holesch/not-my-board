# Metrics

The *Hub* exports metrics at `/metrics`, that can be monitored by
[Prometheus](https://prometheus.io/). Here is a summary of all exposed metrics.

## Gauge `not_my_board_places_registered`

Number of registered places.

## Gauge `not_my_board_places_reserved`

Number of reserved places.

## Gauge `not_my_board_reservation_queue_length`

Number of pending reservation requests.
