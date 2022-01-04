import asyncio
import pulsectl
import logging
import coloredlogs
import re
import time

"""
    A simple Proxy between MPD and it's clients that intercepts the setvol command and uses
    that to control a pulseaudio sources's volume instead.

    Melan, 2020
"""

class mpdprox():
    mpd_server = "localhost"
    mpd_port = 6605
    sinkname = "alsa_output.platform-soc_sound.stereo-fallback"

    log = logging.getLogger("MpdVolumeProxy")

    def __init__(self, loop):
        self.loop = loop
        self.pulse = pulsectl.Pulse()
        self.log.info(f"Primary sink: {self.get_primary_sink()} ({self.get_volume()})")

    def start(self):
        self.server_coro = asyncio.start_server(self.handle_client, '0.0.0.0', 6600, loop=loop)
        self.server = loop.run_until_complete(self.server_coro )
        self.log.info('Serving on {}'.format(self.server.sockets[0].getsockname()))

        try:
            loop.run_forever()

        except KeyboardInterrupt:
            self.log.error("Got keyboard interrupt")

        finally:
            # Close the server
            self.server.close()
            self.loop.run_until_complete(self.server.wait_closed())
            self.loop.close()

    def get_primary_sink(self):
        for sink in self.pulse.sink_list():
            if sink.name == self.sinkname:
                return sink
        self.log.error(f"Failed to find sink named {self.sinkname}")

    def get_volume(self):
        sink = self.get_primary_sink()
        if sink:
            return round(self.pulse.volume_get_all_chans(sink) * 100)
        return 0

    def change_volume(self, procent):
        sink = self.get_primary_sink()
        if sink:
            current_volume = (round(self.pulse.volume_get_all_chans(sink) * 100))

            #Don't actually change the volume above 100
            if procent > 100:
                procent = 100

            if procent < 0:
                procent = 0

            change_to = procent - current_volume
            self.log.info(f"Changing volume to: {change_to}")
            self.pulse.volume_change_all_chans(sink, change_to / 100)

    def extract_numbers_str(self, str):
        return  re.findall(r'\d+', str)

    async def pipe(self, reader, writer, client, intercept=False, intercept_type=0):
        try:
            while not reader.at_eof():
                data = await reader.read(2048)
                if intercept:
                    # Intercept any data from client to mpd
                    if intercept_type == 0:
                        # if not data in [b"idle\n", b"noidle\n", b"status\n"]:
                        #     print(data)
                        # When the client tries to set the volume
                        if data.startswith(b"vol") or data.startswith(b"setvol"):
                            self.log.info(f"Intercepting VOL command: {data.decode('utf-8').strip()}")
                            if len(data.decode("utf-8").split(" ")) > 1:
                                volume = int(self.extract_numbers_str(data.decode("utf-8"))[0])
                                self.log.info(f"Setting pulse volume to {volume}")
                                self.change_volume(volume)
                                client.write(b"OK\n")
                            else:
                                client.write(b"Kut\n")

                        else:
                            writer.write(data)

                    # Intercept any data from mpd to client
                    elif intercept_type == 1:
                        # Intercept the volume flag and replace it with our own
                        if data.startswith(b"volume: "):
                            data = data.decode("utf-8")
                            data = "\n".join(data.split("\n")[1:])
                            data = "volume: " + str(self.get_volume()) + "\n" + data
                            writer.write(data.encode("utf-8"))
                        else:
                            writer.write(data)
                else:
                    writer.write(data)
        finally:
            writer.close()
            client.close()


    async def handle_client(self, local_reader, local_writer):
        """
            Handle when we get a new client, make a connection to the source MPD server
            and start relaying data between them. And intercept the volume command.s
        """
        info = local_writer.get_extra_info('socket')
        self.log.info(f"New client: {str(info)} ")
        try:
            remote_reader, remote_writer = await asyncio.open_connection(self.mpd_server, self.mpd_port, loop=self.loop)
            pipe1 = self.pipe(local_reader, remote_writer, local_writer, intercept=True, intercept_type=0)
            pipe2 = self.pipe(remote_reader, local_writer, local_writer, intercept=True, intercept_type=1)
            await asyncio.gather(pipe1, pipe2)
        except Exception as e:
            self.log.error(f"Exception: {e}")
        finally:
            local_writer.close()

loop = asyncio.get_event_loop()
logging.basicConfig(level=logging.INFO)
coloredlogs.install(level='INFO', fmt="%(asctime)s %(name)s %(levelname)s %(message)s")
while True:
    try:
        mp = mpdprox(loop)
        mp.start()
    except Exception as e:
        print(f"Exception! {e}")
        time.sleep(10)

