"""
Kalkati to GTFS converter
Beware: it simplifies some things.

(c) 2011 Stefan Wehrmeyer http://stefanwehrmeyer.com
License: MIT License

"""

from datetime import date, timedelta
import os
import sys
import xml.sax
from xml.sax.handler import ContentHandler

from coordinates import KKJxy_to_WGS84lalo

#from django.contrib.gis.geos import Point # needed for transformations

timezone = "Europe/Helsinki"

"""
Kalkati Transport modes
1   air
2   train
21  long/mid distance train
22  local train
23  rapid transit
3   metro
4   tramway
5   bus, coach
6   ferry
7   waterborne
8   private vehicle
9   walk
10  other

GTFS Transport Modes
0 - Tram, Streetcar, Light rail.
1 - Subway, Metro.
2 - Rail.
3 - Bus.
4 - Ferry.
5 - Cable car.
6 - Gondola, Suspended cable car.
7 - Funicular.
"""

KALKATI_MODE_TO_GTFS_MODE = {
    "2": "2",
    "21": "2",
    "22": "0",
    "23": "2",
    "3": "1",
    "4": "0",
    "5": "3",
    "6": "4",
    "7": "4"
}


class KalkatiHandler(ContentHandler):
    data = {}

    route_count = 0
    service_count = 0
    routes = {}

    synonym = False
    stop_sequence = None
    trip_id = None
    route_agency_id = None
    route_name = None
    service_validities = None
    service_mode = None
    transmodes = {}

    def __init__(self, gtfs_files):
        self.files = gtfs_files

    def add_stop(self, attrs):
        #point = Point(x=float(attrs['X']), y=float(attrs['Y']), srid=2393) # KKJ3
        #point.transform(4326) # WGS84
        KKJNorthing = float(attrs['X'])
        KKJEasting = float(attrs['Y'])
        KKJLoc = {'P': KKJNorthing, 'I' : KKJEasting}
        WGS84lalo = KKJxy_to_WGS84lalo(KKJin=KKJLoc, zone=3)

        self._store_data("stops", (attrs['StationId'],
                attrs.get('Name', "Unnamed").replace(",", " "),
                str(WGS84lalo['La']), str(WGS84lalo['Lo'])))

    def add_agency(self, attrs):
        self._store_data("agency", (attrs['CompanyId'],
                attrs['Name'].replace(",", " "),
                "http://example.com", timezone))  # can't know

    def add_calendar(self, attrs):
        """This is the inaccurate part of the whole operation!
        This assumes that the footnote vector has a regular shape
        i.e. every week the same service
        """
        service_id = attrs['FootnoteId']
        first = attrs['Firstdate']
        first_date = date(*map(int, first.split('-')))
        vector = attrs['Vector']
        if not len(vector):
            null = ("0",) * 7
            empty_date = first.replace("-", "")
            self._store_data("calendar", (service_id,) + null +
                    (empty_date, empty_date))
            return
        end_date = first_date + timedelta(days=len(vector))
        weekday = first_date.weekday()
        weekdays = [0] * 7
        for i, day in enumerate(vector):
            weekdays[(weekday + i) % 7] += int(day)
        # only take services that appear at least half the maximum appearance
        # this is an oversimplification, sufficient for me for now
        avg = max(weekdays) / 2.0
        weekdays = map(lambda x: "1" if x > avg else "0", weekdays)
        fd = str(first_date).replace("-", "")
        ed = str(end_date).replace("-", "")
        self._store_data("calendar", (service_id,) + tuple(weekdays) +
                (fd, ed))

    def add_stop_time(self, attrs):
        self.stop_sequence.append(attrs['StationId'])
        arrival_time = ":".join((attrs["Arrival"][:2],
                attrs["Arrival"][2:], "00"))
        if "Departure" in attrs:
            departure_time = ":".join((attrs["Departure"][:2],
                    attrs["Departure"][2:], "00"))
        else:
            departure_time = arrival_time
        self._store_data("stop_times", (self.trip_id, arrival_time,
                departure_time, attrs["StationId"], attrs["Ix"]))

    def add_route(self, route_id):
        route_type = "3"  # fallback is bus
        if self.service_mode in self.transmodes:
            trans_mode = self.transmodes[self.service_mode]
            if trans_mode in KALKATI_MODE_TO_GTFS_MODE:
                route_type = KALKATI_MODE_TO_GTFS_MODE[trans_mode]

        self._store_data("routes", (route_id, self.route_agency_id,
                "", self.route_name.replace(",", "."), route_type))

    def add_trip(self, route_id):
        for service_id in self.service_validities:
            self._store_data("trips", (route_id, service_id, self.trip_id,))

    def _store_data(self, key, value):
        if(key not in self.data): self.data[key] = []

        self.data[key].append(value)

    def startElement(self, name, attrs):
        if not self.synonym and name == "Company":
            self.add_agency(attrs)
        elif not self.synonym and name == "Station":
            self.add_stop(attrs)
        elif not self.synonym and name == "Trnsmode":
            if "ModeType" in attrs:
                self.transmodes[attrs["TrnsmodeId"]] = attrs["ModeType"]
        elif name == "Footnote":
            self.add_calendar(attrs)
        elif name == "Service":
            self.service_count += 1
            if self.service_count % 1000 == 0:
                print "Services processed: %d" % self.service_count
            self.trip_id = attrs["ServiceId"]
            self.service_validities = []
            self.stop_sequence = []
        elif name == "ServiceNbr":
            self.route_agency_id = attrs["CompanyId"]
            self.route_name = attrs.get("Name", "Unnamed")
        elif name == "ServiceValidity":
            self.service_validities.append(attrs["FootnoteId"])
        elif name == "ServiceTrnsmode":
            self.service_mode = attrs["TrnsmodeId"]
        elif name == "Stop":
            self.add_stop_time(attrs)
        elif name == "Synonym":
            self.synonym = True

    def endElement(self, name):
        if name == "Synonym":
            self.synonym = False
        elif name == "Service":
            route_seq = "-".join(self.stop_sequence)
            if route_seq in self.routes:
                route_id = self.routes[route_seq]
            else:
                self.route_count += 1
                route_id = str(self.route_count)
                self.routes[route_seq] = route_id
                self.add_route(route_id)
            self.add_trip(route_id)
            self.trip_id = None
            self.stop_sequence = None
            self.route_agency_id = None
            self.route_name = None
            self.service_validities = None
            self.service_mode = None


def init_files(files):
    fields = {
        "agency": (u'agency_id', u'agency_name', u'agency_url',
            u'agency_timezone',),
        "stops": (u'stop_id', u'stop_name', u'stop_lat', u'stop_lon',),
        "routes": (u"route_id", u"agency_id", u"route_short_name",
            u"route_long_name", u"route_type",),
        "trips": (u"route_id", u"service_id", u"trip_id",),
        "stop_times": (u"trip_id", "arrival_time", "departure_time",
            u"stop_id", u"stop_sequence",),
        "calendar": (u'service_id', u'monday', u'tuesday', u'wednesday',
            u'thursday', u'friday', u'saturday', u'sunday', u'start_date',
            u'end_date',)
    }

    for name in files:
        write_values(files, name, fields[name])


def write_values(files, name, values):
    files[name].write((u",".join(values) + u"\n").encode('utf-8'))


def main(filename, directory):
    names = ['stops', 'agency', 'calendar', 'stop_times', 'trips', 'routes']
    files = {}
    for name in names:
        files[name] = file(os.path.join(directory, "%s.txt" % name), "w")

    handler = KalkatiHandler(files)
    xml.sax.parse(filename, handler)

    init_files(files)

    # TODO: transform data now

    for k in handler.data:
        for item in handler.data[k]:
            write_values(files, k, item)

    for name in names:
        files[name].close()

if __name__ == '__main__':
    try:
        filename = sys.argv[1]
        output = sys.argv[2]
    except IndexError:
        sys.stderr.write(
                "Usage: %s kalkati_xml_file output_directory\n" % sys.argv[0])
        sys.exit(1)
    main(filename, output)
