async def test_tutorial(vms):
    async with vms.hub.ssh_task("not-my-board hub", "hub", wait_ready=True):
        async with vms.hub.ssh_task(
            "python3 -m http.server -d ./src/tests/tutorial -b localhost 8080",
            "http_server",
        ):
            async with vms.hub.ssh_task(
                "not-my-board export http://localhost:2092 ./src/tests/tutorial/tutorial-tcp-place.toml",
                "export",
                wait_ready=True,
            ):
                async with vms.hub.ssh_task_root(
                    "not-my-board agent http://localhost:2092", "agent", wait_ready=True
                ):
                    await vms.hub.ssh(
                        "doas not-my-board attach ./src/tests/tutorial/tutorial-tcp.toml"
                    )

                    await vms.hub.ssh_poll("nc -z localhost 8080")
                    result = await vms.hub.ssh(
                        "wget -qO - http://localhost:8081/hello", prefix="wget"
                    )
                    assert result.stdout.rstrip() == "Hello, World!"
