# tado-local-proxy
Exposing the Tado homekit api as reusable rest api

With TADO rate limiting the current cloud api, such as used by [libtado](https://github.com/germainlefebvre4/libtado), we need an alternate reliable way to access TADO data. The recommended way from the TADO side is using the homekit api, but this is currently not that easy accessible.

Most online pages recommend setting up [homeassistant](https://www.home-assistant.io/), and then a bridge from that to whatever system you use to manage your home automation. In my case that would be [Domoticz](http://domoticz.com). And to connect that to HomeAssistant I would need to setup an mqtt bridge. 
And all of that needs custom configuration, etc. For all Tado devices separately.

I don't like that route as setting up homeassistant for one appliance is **a lot of overhead**, so I tried to find another solution. And that is this local proxy

## a local proxy
I created a minimalistic python proxy that uses an async connection to the tado bridge, just like how homeassistant does things. With that I feed a local sqlite database to provide some historic information and feed a local rest API.

### Single homekit connection
One limitation of this, is that the TADO internet bridge currenly allows only a single homekit connection. So to use this proxy you will need to give access to this connection. Eventually we may be able to resolve this limitation by exposing the proxy as its own homekit device. But for now that is out of my scope. (PRs very welcome ;-))

Things are currently in the very early stages of development. I'm able to connect to the bridge and expose the current data

To setup things you run the first time

`$ python proxy.py --state ~/.state.db --bridge-ip 192.168.0.233 --pin 123-45-678 --port 8091`

This does the initial pairing and stores the connection state in state.db and then starts listening on port 8091

After this you can restart the service as just

`$ python proxy.py --state state.db --port 8091`

If the internet bridge is already paired this will fail. You will have to clear the initial pairing from the current controller or by resetting the pairing on the bridge. On my V3 controller this works by pressing the small reset button on the back for about ten seconds. See this [TADO Article](https://support.tado.com/en/articles/3387334-how-can-i-reset-the-homekit-configuration-of-the-internet-bridge)


