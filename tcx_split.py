import argparse
import datetime
import logging
import re
from StringIO import StringIO
import time
import zipfile



parser = argparse.ArgumentParser()
parser.add_argument("-f", "--filename", help="The name of the file to parse")



IN_HEADER = 99
IN_XML = 0
IN_LAP = 1
IN_TRACK = 2
IN_TRACKPOINT = 3
IN_FOOTER = 5

RE_ID = re.compile(r'<Id>([^<]+)</Id>')
RE_LAP_START_TIME = re.compile(r'StartTime="([^"]+)"')
RE_TIME = re.compile(r'<Time>([^<]+)</Time>')
RE_SPACE = re.compile(r'^( +)')
RE_DISTANCE = re.compile(r'<DistanceMeters>([0-9\.]+)</DistanceMeters>')
RE_TOTALTIME = re.compile(r'<TotalTimeSeconds>([0-9\.]+)</TotalTimeSeconds>')




def parse_lap_time(line):
    '''
    @param[IN] line The line containing the 'StartTime="....."'
    @return The datetime object
    '''
    str_time = RE_LAP_START_TIME.search(line).groups(1)[0]
    return datetime.datetime.strptime(str_time, Workout.TIME_FMT)

def parse_trackpoint_time(line):
    '''
    @param[IN] line The line containing the '<Time>...</Time>'
    @return The datetime object
    '''
    str_time = RE_TIME.search(line).groups(1)[0]
    return datetime.datetime.strptime(str_time, Workout.TIME_FMT)

def parse_distance(line):
    '''
    @param[IN] line The line containing the '<DistanceMeters>...<DistanceMeters>'
    @return The distance as float
    '''
    str_distance = RE_DISTANCE.search(line).groups(1)[0]
    #print "parse_lap_distance: ", s
    return float(str_distance)



class G_XML:
    def __init__(self):
      self.parent = None

    def parse(line):
        return None

class Trackpoint(G_XML):
    def __init__(self, parent, line):
        self.parent = parent
        self.lines = [line]
        self.time = None
        self.distance_offset = 0

    def parse(self, garmin_xml):
        #print "Trackpoint:parse"
        while len(garmin_xml):
            line = garmin_xml.pop(0)
            if '</Trackpoint' in line:
                self.lines.append(line)
                return
            else :

                if '<Time' in line:
                    self.time = parse_trackpoint_time(line)
                elif '<DistanceMeters>' in line:
                    self.distance = parse_distance(line)
            self.lines.append(line)

    def verify(self):
       ''' Nothing to do '''

    def update(self):
        for i, line in enumerate(self.lines):
            if '<DistanceMeters' in line:
                #print "Trackpoint:update: offset: %f" % self.distance_offset
                distance = self.distance - self.distance_offset
                self.lines[i] = RE_DISTANCE.sub("<DistanceMeters>" + str(distance) + "</DistanceMeters>", line)

    def writeTo(self, f):
        for line in self.lines:
            f.write(line)



class Track(G_XML):
    def __init__(self, parent, line=None, track=None, offset=0.0):
        self.parent = parent
        self.trackpoints = []
        self.total_distance = 0
        self.need_update = (None != track)
        if line != None:
            self.lines = [line]
            self.distance_offset = 0
        elif track != None:
            self.parent = parent
            self.lines = track.lines[:]
            self.distance_offset = offset
            print "Track:__init__: offset %f" % self.distance_offset

    def parse(self, garmin_xml):
        while len(garmin_xml):
            line = garmin_xml.pop(0)
            #print "Track:parse: line=%s" % line,
            if '<Trackpoint' in line:
                trackpoint = Trackpoint(self, line)
                trackpoint.parse(garmin_xml)
                self.trackpoints.append(trackpoint)
            elif '</Track' in line:
                self.lines.append(line)
                return
            else :
                self.lines.append(line)

    def verify(self, max_secs_bw_laps):
        #print "Track:verify:"
        prev_time = self.trackpoints[0].time
        for i, tp in enumerate(self.trackpoints):
            #print "Track:verify: i:%d tp.time:%s  prev_time:%s" % (i, tp.time.strftime(Workout.TIME_FMT), prev_time.strftime(Workout.TIME_FMT))
            if (tp.time - prev_time).total_seconds() > max_secs_bw_laps:
                #print "Track:verify: Found a big difference: current:%s  prev:%s" % (tp.time.strftime(Workout.TIME_FMT), prev_time.strftime(Workout.TIME_FMT))
                #print "Track:verify: distance current: %f prev:%s" % (tp.distance, self.trackpoints[i-1].distance)
                return self.split(i)
            prev_time = tp.time
        return None

    def update(self):
        if self.need_update:
            for tp in self.trackpoints:
                tp.distance_offset = self.distance_offset
                tp.update()

    def writeTo(self, f):
        for line in self.lines[:-1]:
            f.write(line)
        for tp in self.trackpoints:
            tp.writeTo(f)
        f.write(self.lines[-1])

    def split(self, i):
        '''
        Split trackpoints of the the current Track at index i and move
        the trackpoins over to a new Track and return it
        '''
        print "Track:split: Initial number of trackpoints: %d i:%d" % (len(self.trackpoints), i)
        new_tr = Track(self.parent, track=self, offset=self.trackpoints[i-1].distance)
        new_tr.trackpoints = self.trackpoints[i:]
        self.trackpoints = self.trackpoints[:i]
        self.need_update = True
        print "Track:split: Old: %d  New: %d" % (len(self.trackpoints), len(new_tr.trackpoints))
        return new_tr




class Lap(G_XML):
    def __init__(self, parent, line=None, lap=None, idx=0):
        '''
        @param idx The idx where to split. The lap will have the track [:idx] and the new lap [idx:]
        '''
        self.parent = parent
        self.beforeTrackLines = None
        self.tracks = None
        self.afterTrackLines = []
        self.need_update = False
        if line != None:
            #print "Lap:constructor: line=%s" % line,
            self.beforeTrackLines = [line]
            self.tracks = []
            self.start_time = parse_lap_time(line)
            self.distance = 0
        elif lap != None:
            self.parent = parent
            self.beforeTrackLines = lap.beforeTrackLines[:]
            self.afterTrackLines = lap.afterTrackLines[:]
            self.tracks = lap.tracks[idx:]
            lap.tracks = lap.tracks[:idx]
            self.need_update = True
            lap.need_update = True
            self.start_time = self.tracks[0].trackpoints[0].time
            print "New Lap: tp[0] time: %s  line[1]: %s" % (self.tracks[0].trackpoints[0].time.strftime(Workout.TIME_FMT), self.tracks[0].trackpoints[0].lines[1])
            str_time = self.start_time.strftime(Workout.TIME_FMT)
            print "New Lap: start_time: %s" % str_time
            self.beforeTrackLines[0] = RE_LAP_START_TIME.sub('StartTime="' + str_time + '"', self.beforeTrackLines[0])
            print "New Lap: check first line: %s" % self.beforeTrackLines[0]
            self.distance = -1
            self.need_update = True

    def parse(self, garmin_xml):
        while len(garmin_xml):
            line = garmin_xml.pop(0)
            #print "Lap:parse: line= %s" % line,
            if '<Track' in line:
                #print "Lap:parse: Ici 2"
                track = Track(self, line)
                self.tracks.append(track)
                track.parse(garmin_xml)
            elif '</Lap>' in line:
                #print "Lap:parse: Ici 3"
                self.afterTrackLines.append(line)
                return self.parent
            elif len(self.tracks) == 0:
                #print "Lap:parse: Ici 4"
                if '<DistanceMeters>' in line:
                    self.distance = parse_distance(line)
                self.beforeTrackLines.append(line)
            else:
                self.afterTrackLines.append(line)


    def verify(self, max_secs_bw_laps):
        '''
        Check if all the tracks are continuous.
        If not then create a new Lap and move the tracks over
        '''
        #print "Lap:verify Nb tracks: %d" % len(self.tracks)
        current = self
        prev_time = current.tracks[0].trackpoints[0].time
        i = 0
        for i, tr in enumerate(self.tracks):
            #print "Lap:verify: i=%d" % i
            new_tr = tr.verify(max_secs_bw_laps)
            if new_tr:
                #print "Lap:verify: inserting the new track at %d" % (i+1)
                self.tracks.insert(i+1, new_tr)
                return self.split(i+1)
                '''
                if (tr.trackpoints[0].time - prev_time).total_seconds() > max_secs_bw_laps:
                    print "Lap:verify: Found a Big difference: %s" % tr.time.strftime(Workout.TIME_FMT)
                    current = Lap(self.parent, Lap=self)
                    current.tracks = self.tracks[i:]
                    self.tracks = self.tracks[:i]
                    current.start_time = self.tracks[0].time
                    return current

                prev_time = tr.time
                if -1 == self.distance:
                    self.distance = distance
                    for line in self.beforeTracklines:
                        if '<DistanceMeters' in line:
                            line = RE_DISTANCE.sub("<DistanceMeters>" + str(distance) + "</DistanceMeters>", line)
                        elif '<TotalTimeSeconds' in line:
                            line = RE_TOTALTIME.sub("<TotalTimeSeconds>" + str(time) + "</TotalTimeSeconds>", line)
                '''
                return None


    def update(self):
        if not self.need_update:
            return
        print "\n"
        print "Lap:Update: Start Time: %s" % self.start_time.strftime(Workout.TIME_FMT)
        for tr in self.tracks:
            tr.update()
        last_track = self.tracks[-1]
        last_tp = last_track.trackpoints[-1]
        distance = last_tp.distance
        if len(last_track.trackpoints) >= 2:
            print "Lap:Update: Ici 1 : Tp.time: %s" % last_track.trackpoints[0].time.strftime(Workout.TIME_FMT)
            distance -= last_track.trackpoints[0].distance
        elif len(self.tracks) >= 2:
            print "Lap:Update: Ici 2"
            distance -= self.tracks[-2].trackpoints[-1].distance

        print "Lap:update: Track Time: %s distance; %f" % (last_tp.time.strftime(Workout.TIME_FMT), last_tp.distance)
        print "Lap:update: Lap distance: %f" % distance
        for i, line in enumerate(self.beforeTrackLines):
            if '<DistanceMeters' in line:
                self.beforeTrackLines[i] = RE_DISTANCE.sub("<DistanceMeters>" + str(distance) + "</DistanceMeters>", line)
            elif '<TotalTimeSeconds' in line:
                    seconds = (last_tp.time - self.start_time).total_seconds()
                    print "Lap:update: LastTime: %s  StartTime: %s" % (last_tp.time .strftime(Workout.TIME_FMT), self.start_time.strftime(Workout.TIME_FMT))
                    print "Lap:update: TotalTimeSeconds: %f" % seconds
                    self.beforeTrackLines[i] = RE_TOTALTIME.sub("<TotalTimeSeconds>" + str(seconds) + "</TotalTimeSeconds>", line)

    def writeTo(self, f):
        #print "Lap:writeTo: Nb BeforeLapLines: %d:" % (len(self.beforeTrackLines))
        for line in self.beforeTrackLines:
            f.write(line)
        for tr in self.tracks:
            tr.writeTo(f)
        for line in self.afterTrackLines:
            f.write(line)


    def split(self, i):
        '''
        Split track of the the current Lap at index i and move
        the track over to a new Lap and return it
        '''
        #print "Lap:split: Initial number of tracks: %d i:%d" % (len(self.tracks), i)
        new_lap = Lap(self.parent, lap=self, idx=i)
        #print "Lap:split: Old: %d  New: %d" % (len(self.tracks), len(new_lap.tracks))
        return new_lap


class Header(G_XML):
    def __init__(self, parent):
        self.parent = parent
        self.lines = []

    def add(self, line):
        self.lines.append(line)
        return self

    def update_id(self, time):
        #print "Header: update_id"
        for i, line in enumerate(self.lines):
            if '<Id' in line:
                #print "Header:update_id: Old: %s" % line
                str_time = time.strftime(Workout.TIME_FMT)
                self.lines[i] = RE_ID.sub('<Id>' + str_time + '</Id>', line)
        #for line in self.lines:
        #    if '<Id' in line:
                #print "Header:update_id: New: %s" % line


    def writeTo(self, f):
        for line in self.lines:
            f.write(line)



class Footer(G_XML):
    def __init__(self, parent):
        self.parent = parent
        self.lines = []

    def add(self, line):
        self.lines.append(line)
        return self

    def writeTo(self, f):
        for line in self.lines:
            #print "Footer::writeTo: %s" % line,
            f.write(line)


class Workout(G_XML):
    TIME_FMT = '%Y-%m-%dT%H:%M:%S.000Z'

    def __init__(self, parent, workout=None):
        parent.workouts.append(self)
        self.parent = parent
        self.laps = []
        self.footer = None
        self.need_update = False
        if workout == None:
            self.header = Header(self)
        else:
            print "Creating a new workout"
            self.header = workout.header
            self.footer = workout.footer
            self.need_update = True


    def parse(self, garmin_xml):
        while len(garmin_xml):
            line = garmin_xml.pop(0)
            #print "Workout:parse %s" % line,
            if '<Lap' in line:
                lap = Lap(self, line)
                self.laps.append(lap)
                lap.parse(garmin_xml)
                self.footer = Footer(self)
            elif len(self.laps) == 0 :
                self.header.add(line)
            else:
                #print "Workout:parse: footer.add: %s" % line,
                self.footer.add(line)


    def verify(self, max_secs_bw_laps):
        #print "Workout:verify: StartTime: ", self.laps[0].start_time.strftime(Workout.TIME_FMT)
        prev_lap_time = self.laps[0].start_time
        for i, lap in enumerate(self.laps):
            new_lap = lap.verify(max_secs_bw_laps)
            if new_lap:
                self.laps.insert(i+1, new_lap)
                return self.split(i+1)
            cur_lap_time = lap.start_time
            diff = (cur_lap_time - prev_lap_time).total_seconds()
            #print "Workout:verify: %d Lap Time: %s  diff=%f max=%f" % (i, lap.start_time.strftime(Workout.TIME_FMT), diff, max_secs_bw_laps)
            if  diff > max_secs_bw_laps:
                #print "Workout::verify: Should create a new workout"
                #print "Workout:verify: A=%s  B=%s" %(cur_lap_time.strftime(Workout.TIME_FMT), prev_lap_time.strftime(Workout.TIME_FMT))
                return self.split(i)
            else:
                new_lap = lap.verify(max_secs_bw_laps)
                if new_lap != None:
                    return self.split(i)
            prev_lap_time = cur_lap_time


    def update(self):
        #print "Workout:update: %s" % self.laps[0].start_time.strftime(Workout.TIME_FMT)
        for lap in self.laps:
            lap.update()
        if self.need_update:
            #"Workout:update need_update"
            lap_start_time = self.laps[0].start_time
            self.header.update_id(lap_start_time)


    def writeTo(self, f):
        self.header.writeTo(f)
        for lap in self.laps:
            lap.writeTo(f)
        self.footer.writeTo(f)


    def split(self, i):
        '''
        Split lap of the the current Workout at index i and move
        the laps over to a new Workout and return it
        '''
        print "Workout:split: Initial number of lap: %d i:%d" % (len(self.laps), i)
        new_workout = Workout(self.parent, self)
        new_workout.laps = self.laps[i:]
        self.laps = self.laps[:i]
        print "Workout:split: Old: %d  New: %d" % (len(self.laps), len(new_workout.laps))
        return new_workout



class My:
    def __init__(self):
        self.workouts = []

def split_xml(garmin_xml, max_secs_bw_laps):
    # parse the XML (beautiful soup was breaking it :( ... garmin overly sensitive to whitespace?)
    my = My()
    workout = Workout(my)
    workout.parse(garmin_xml)

    for i, w in enumerate(my.workouts):
        new_w = w.verify(max_secs_bw_laps)
        if new_w:
            my.workouts.insert(1+i, new_w)


    if len(my.workouts) <= 1:
        print "%d workout(s) found - won't generate any splits" % len(my.workouts)
        return

    # save each workout
    #zipstream = 'tata.zip'
    #zip_file = zipfile.ZipFile(zipstream, "w")
    #header = '\n'.join(header)
    #footer = '\n'.join(footer)
    for workout in my.workouts:
        workout.update()
        fn = workout.laps[0].start_time.strftime('%Y-%m-%d %I:%M:%S %p.tcx')
        f = open(fn, 'w')
        workout.writeTo(f)
        f.close()
        #zip_file.writestr(fn, xml)
        #logging.info('zipped workout (%dB) containing %d laps to %s' % (len(xml), len(workout), fn))

    #zip_file.close()


if __name__ == '__main__':
    args = parser.parse_args()
    filename = 'GTD2014.tcx'
    f = open(args.filename)
    garmin_xml = f.readlines()
    max_secs = 7200.0
    split_xml(garmin_xml, max_secs)
