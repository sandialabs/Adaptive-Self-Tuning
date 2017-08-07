from sys import argv
from InputReader.ConfigReader import ConfigReader
from InputReader.NeighborhoodReader import read as nl_reader
from InputReader.GtReader import read as gt_reader
from Tools.MajorityRules import MajorityRules
from Tools import EventScorer,DetectionScorer,RawDetectionScorer,StationScorer
import Tools.Seismic as seismic
import math

def run_with_config(config):
    """
    Run DecisionMakers based on the parameters specified
    in a configuration file
    @params:
        config      a ConfigReader object
        quiet       if 'True', no output to screen. Default is 'True'
    @return:
        score       returns a str object that contains the score
                    of how well the DecisionMakers did compared
                    to ground truth values
    """
    data_dir = config['Data_dir']
    data_files = config['Data_files']
    neighbor_list = config['Neighbor_list']
    gt_file = config['Ground_truths',None]
    minGtSnr = float(config['minGtSnr'])
    trigger_file = config['Trigger_File',None]
    
    sta_win = float(config['STA_Window',1])
    lta_win = float(config['LTA_Window',30])
    trigger_level = float(config['Trigger_Level',2.0])
    trigger_duration = float(config['Trigger_Duration',3])
    reset_level = float(config['Reset_Level',1.5])
    reset_duration = float(config['Reset_Duration',3])
    
    init_tls = config['Init_TLs',None]
    
    dynamic_agree = int(config['Dynamic_Agree',2])
    score_agree = int(config['Score_Agree',2])
    score_window = float(config['Score_Window',2])
    
    step_size = float(config['Step_Size',0.1])
    decay_rate = float(config['Decay_Rate',-0.002])
    
    bandpass_high = config['Bandpass_high',None]
    bandpass_low = config['Bandpass_low',None]
    if bandpass_low != None and bandpass_high != None:
        bandpass_high = int(bandpass_high)
        bandpass_low = int(bandpass_low)
    
    time_step = float(config['Time_step'])
    start = float(config['Start']) * 3600.0
    score_delay = float(config['Score_delay']) * 3600.0
    duration = float(config['Duration']) * 3600.0
    
    quiet = bool(int(config['Quiet',1]))
        
    stations = seismic.getStations(data_dir,data_files,bandpass_low,bandpass_high,quiet=quiet)
    sr = stations[0].stats.sampling_rate
    start_time = float(stations[0].stats.starttime)
    arrivals,TLs = run(stations,neighbor_list,time_step,start,
                   duration,sta_win,lta_win,
                   trigger_duration,trigger_level,reset_level,reset_duration,step_size,
                   decay_rate,starting_tls=init_tls,
                   minStaAgree=dynamic_agree,TRIGGER_FILE=trigger_file,quiet=quiet)
    
    if gt_file == None:
        return arrivals,TLs
    else:
        gts = gt_reader(gt_file,start_time,sr,start*sr,(start+duration)*sr,minGtSnr)
        #return EventScorer.score(arrivals,gts,score_window,score_delay=score_delay,minStaAgree=score_agree,sr=sr),TLs
        return DetectionScorer.score(arrivals,gts,score_window,score_delay=score_delay,sr=sr),TLs
        #return StationScorer.score(arrivals,gts,score_window,score_delay=score_delay,sr=sr), TLs
        #return RawDetectionScorer.score(arrivals,gts,score_window,score_delay=score_delay,sr=sr),TLs
    
def run(stations,nl_filename,time_step,start,duration,sta_win,lta_win,
        trigger_duration,trigger_level,reset_level,reset_duration,step_size,decay_rate,minStaAgree=2,
        starting_tls=None,TRIGGER_FILE=None,quiet=True):
    """
    Runs individual DecisionMakers over all the inputed waveforms
    and returns the arrivals times
    @params:
        stations            a list of Traces outputted from obspy.read
        nl_filename         the name of the file that contains the 
                            neighborhood lists
        time_step           number of seconds to process over for agreement
        start               how many seconds into the waveforms to start
                            processing on
        duration            how many seconds to process over
        sta_win             the STA window size in seconds
        lta_win             the LTA window size in seconds
        trigger_duration    how long a dection needs to be above the trigger
                            level in seconds
        trigger_level       the trigger onset threshold
        reset_level         the trigger offset threshold (currently not used)
        step_size           maximum step for changing action values 
        minStaAgree         minimum number of stations for agreement.
                            Default is '2'
        quiet               if 'True', no output to screen. Default is 'True'
    @return:
        arrivals            a list of arrivals generated by the DecisionMakers
    """
    sr = stations[0].stats.sampling_rate
    reset_dur = reset_duration * sr
    levels = seismic.getStaLtaValues(stations,start,duration,sta_win,lta_win,sr,quiet=quiet)
    neighbor_list = nl_reader(nl_filename)
    DMs = initDecisionMakers(neighbor_list,trigger_level,step_size,decay_rate,init_tls_file=starting_tls)
       
    #import code
    #code.interact(local=locals()) 
    
    tl_file = open(TRIGGER_FILE,'w')
    tl_file.write('time')
    for sta in stations:
        tl_file.write("," + sta.stats.station + '-' + sta.stats.channel)
    tl_file.write('\n')
    
    last_detect = {}
    for sta in levels:
        last_detect[sta] = -1 * (reset_duration + 2)
        
    arrivals =[]
    curr_time = int(start*sr)
    while curr_time < int((start+duration)*sr) and curr_time < len(stations[0]):
        trigs,avg_snr,temp_arrivals = seismic.getDetections(levels,
                                                            curr_time,
                                                            sr,
                                                            time_step,
                                                            trigger_duration,
                                                            reset_duration,
                                                            DMs)
        if step_size != 0.0:
            actual_time = seismic.findFirstDetect(temp_arrivals)
            if actual_time != None:
                curr_time = actual_time
                del trigs,avg_snr,temp_arrivals
                trigs,avg_snr,temp_arrivals = seismic.getDetections(levels,
                                                                    curr_time,
                                                                    sr,
                                                                    time_step,
                                                                    trigger_duration,
                                                                    reset_duration,
                                                                    DMs)
        
        HAPPENED = removeDetections(trigs,temp_arrivals,last_detect,reset_dur)
        
        if step_size != 0.0:     
            new_actions = updateActions(trigs,avg_snr,DMs)
            if not quiet or TRIGGER_FILE != None:
                printActions(curr_time,new_actions,toFile=tl_file)
        
        nDef = len(temp_arrivals)
        for tt in temp_arrivals:
            tt['nDef'] = nDef
        
        if nDef >= minStaAgree:
            arrivals += temp_arrivals
        #arrivals += temp_arrivals
        curr_time += int(time_step * sr)
        del trigs,avg_snr,temp_arrivals 
    end_tls = {}
    for sta in DMs:
        end_tls[sta] = DMs[sta].prev_action
    return arrivals,end_tls

def removeDetections(trigs,temp_arrivals,last_detect,reset_duration):
    """
    Removes detections if the curr detections is not past the reset_duration.
    """
    DELETED = False
    i = 0
    while i < len(temp_arrivals):
        sta = temp_arrivals[i]['station']
        arr_time = temp_arrivals[i]['time']
        if (last_detect[sta] + reset_duration) > arr_time:
            trigs[sta] = 0
            del(temp_arrivals[i])
            DELETED = True
        else:
            i += 1
        
        last_detect[sta] = arr_time
    
    return DELETED
    
def updateActions(triggers,avg_snr,DMs):
    """
    Updates the DecisionMakers actions based on what the
    network of sensors saw
    @params:
        triggers        a dictionary where the keys are the
                        station names and the values are a
                        '1' if the station detected and a
                        '0' if the station did not detect
        avg_snr         a dictionary where the keys are the
                        station names and the values are
                        the average snr for a given station
                        over the current time_step
        DMs             a dictionary of Decision Makers where
                        the keys are the station names and
                        the values are the corresponding 
                        DecisionMakers
    @return:
        new_actions     the update action values
    """
    new_actions = {}
    for sta in DMs:
        new_actions[sta] = DMs[sta].getAction(triggers,avg_snr[sta])

    return new_actions
    
def printActions(time,actions,toFile=None):
    """
    Prints out the current action for each station
    @params:
        time        the current time step
        actions     a dictionary of current actions where
                    the keys are the station names
        toFile      file object to write to.  Default is 'None'
                    and will print to screen
    @return:
        None
    """
    s = str(time)
    for sta in actions:
        s += "," + str(actions[sta])
    if toFile != None:
        toFile.write(s+'\n')
    else:
        print s

def initDecisionMakers(n_list,init_val,step_size,decay_rate,init_tls_file=None):
    """
    Sets up the DecisionMakers (i.e. MajorityRules Objects)
    @params:
        n_list      neighborhood list for each station
        init_val    starting action value
        step_size   maximum step for changing action values
    @return:
        DMs         a dictionary of DecisionMakers where the
                    keys are the station (sta) names
    """
    init_tls = {}
    if init_tls_file:
        for line in open(init_tls_file,'r').readlines():
            l = line.strip().split(',')
            init_tls[l[0]] = float(l[1])
        
    use_custom_vals = False
    if len(init_tls) == len(n_list):
        use_custom_vals = True
    DMs = {}
    for sta,nl in n_list.iteritems():
        if use_custom_vals:
            temp_tl = init_tls[sta]
        else:
            temp_tl = init_val
        DMs[sta] = MajorityRules(sta,temp_tl,nl,ss=step_size,dr=decay_rate)
    return DMs

if __name__ == '__main__':
    """
    Short Script that takes in 1 arg from the command line
    which is the file path for the configuration file
    """
    config = ConfigReader()
    config.read(argv[1])
    
    arrival_output_file = config['Arrival_File','ARRIVAL_OUTPUT.dat']
    
    result,tls = run_with_config(config)
    
    if type(result) == str:
        results = result.split("|")
        print config['Trigger_Level',2.0] + ',' + results[0] + '\n' + config['Trigger_Level',2.0] + ',' + results[1]
    else:
        result = sorted(result,key= lambda x: x['time'])
        for r in result:
            print r
        print "size: " + str(len(result))
        f = open(arrival_output_file,'w')
        f.write("STA-CHAN,time,nDef,snr\n")
        for r in result:
            #STA,CHAN = r['station'].split('-')
            #f.write(r['station'] + ',' + str(r['time'] / 200.0 + 1230163203.03) + ',' + str(r['nDef']) + ',' + str(r['snr']) + '\n') 
            f.write(r['station'] + "," + str(r['time']) + "," + str(r['nDef']) + "," + str(r['snr']) + '\n')
        f.close()
        #import code
        #code.interact(local=locals())
