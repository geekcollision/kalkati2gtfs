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

from django.contrib.gis.geos import Point # needed for transformations

timezone = "Europe/Helsinki"

"""
Kalkati Transport modes
1	air
2	train
21	long/mid distance train
22	local train
23	rapid transit
3	metro
4	tramway
5	bus, coach
6	ferry
7	waterborne
8	private vehicle
9	walk
10	other

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
    agency_fields = (u'agency_id', u'agency_name', u'agency_url',
            u'agency_timezone',)
    stops_fields = (u'stop_id', u'stop_name', u'stop_lat', u'stop_lon',)
    routes_fields = (u"route_id", u"agency_id", u"route_short_name",
            u"route_long_name", u"route_type",)
    trips_fields = (u"route_id", u"service_id", u"trip_id",)
    stop_times_fields = (u"trip_id", "arrival_time", "departure_time",
            u"stop_id", u"stop_sequence",)
    calendar_fields = (u'service_id', u'monday', u'tuesday', u'wednesday',
            u'thursday', u'friday', u'saturday', u'sunday', u'start_date',
            u'end_date',)
    
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
        for name in gtfs_files:
            self.write_values(name, getattr(self, "%s_fields" % name))

    def write_values(self, name, values):
        self.files[name].write((u",".join(values) + u"\n").encode('utf-8'))
        
    def add_stop(self, attrs):
        point = Point(x=float(attrs['X']), y=float(attrs['Y']), srid=2393) # KKJ3
        point.transform(4326) # WGS84
        self.write_values("stops", (attrs['StationId'],
                attrs.get('Name', "Unnamed").replace(",", " "),
                str(point.y), str(point.x)))

    def add_agency(self, attrs):
        self.write_values("agency", (attrs['CompanyId'],
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
            self.write_values("calendar", (service_id,) + null +
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
        self.write_values("calendar", (service_id,) + tuple(weekdays) +
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
        self.write_values("stop_times", (self.trip_id, arrival_time,
                departure_time, attrs["StationId"], attrs["Ix"]))
        
    def add_route(self, route_id):
        route_type = "3"  # fallback is bus
        if self.service_mode in self.transmodes:
            trans_mode = self.transmodes[self.service_mode]
            if trans_mode in KALKATI_MODE_TO_GTFS_MODE:
                route_type = KALKATI_MODE_TO_GTFS_MODE[trans_mode]
        
        self.write_values("routes", (route_id, self.route_agency_id,
                "", self.route_name.replace(",", "."), route_type))
                
    def add_trip(self, route_id):
        for service_id in self.service_validities:
            self.write_values("trips", (route_id, service_id, self.trip_id,))
                
    def startElement(self, name, attrs):
        if not self.synonym and name == "Company":
            self.add_agency(attrs)
        elif not self.synonym and name == "Station":
            self.add_stop(attrs)
        elif not self.synonym and name == "Trnsmode":
            if "Modetype" in attrs:
                self.transmodes[attrs["TrnsmodeId"]] = attrs["Modetype"]
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


def main(filename, directory):
    names = ["stops", "agency", 'calendar', 'stop_times', 'trips', 'routes']
    files = {}
    for name in names:
        files[name] = file(os.path.join(directory, "%s.txt" % name), "w")

    handler = KalkatiHandler(files)
    xml.sax.parse(filename, handler)

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