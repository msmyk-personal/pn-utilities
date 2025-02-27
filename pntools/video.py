"""
Tools for working with videos. It contains some wrappers that call  for processing videos.

Use the gui module for browsing videos.
"""
import os
import re
import subprocess
import urllib
import time
from pathlib import Path

CLIP_FOLDER = 'C:\\data\\_clipcollection'


def probe(vid_file):
    """return the output of ffprobe"""
    assert os.path.exists(vid_file)
    return subprocess.getoutput(f'ffprobe -hide_banner -show_entries stream=duration "{vid_file}"')

def get_sr(vid_file):
    """Parse ffprobe output to get sampling rate of a video"""
    x = probe(vid_file)
    try:
        sr = round(float(re.search('fps, (.+?) tbr,', x).group(1)))
    except AttributeError:
        sr = None
    return sr

def get_dur(vid_file):
    """Parse ffprobe output to get the duration of a video file"""
    return float(probe(vid_file).split('[STREAM]')[-1].split('[/STREAM]')[0].split('duration=')[-1].rstrip('\n'))

def detect_black_frames(vid_file):
    """Detect black frames in a video file using ffmpeg.

    Returns a dictionary with lists of start times, end times, duration, and the file name that was given as input
    """
    assert os.path.exists(vid_file)

    import ffmpeg

    def find_float(str, pre):
        return float(re.search(f'(?<={pre})[.\\d]+', str).group(0))
    bd = subprocess.getoutput(f'ffmpeg -i "{vid_file}" -vf blackdetect=d=0:pix_th=.01 -f rawvideo -y /NUL')
    blackframe_segments = [b for b in bd.splitlines() if 'blackdetect' in b]
    start = [find_float(seg, 'black_start:') for seg in blackframe_segments]
    end = [find_float(seg, 'black_end:') for seg in blackframe_segments]
    duration = [find_float(seg, 'black_duration:') for seg in blackframe_segments]
    return {'start': start, 'end': end, 'duration': duration, 'fname': vid_file}

def interp_black_frames(vid_file, vid_output=None, overwrite=False):
    """Interpolate black frames in a video"""
    import ffmpeg

    if vid_output is None:
        vid_output = os.path.join(Path(vid_file).parent, f'{Path(vid_file).stem} bfinterp{Path(vid_file).suffix}')
    if (not os.path.exists(vid_output)) or overwrite:
        vid_sr = get_sr(vid_file)
        this_cmd = f'ffmpeg -y -i "{vid_file}" -vf blackframe=0,metadata=select:key=lavfi.blackframe.pblack:value=50:function=less,framerate=fps={vid_sr} -c:v h264_nvenc "{vid_output}"'
        return subprocess.getoutput(this_cmd)
    return "Did not interpolate."

def make_montage2x2(vid_files, vid_output=None, aud_file=None, overwrite=False):
    """
    Create a 2x2 montage with 4 video files using ffmpeg xstack
    
    Inputs:
        vid_files - list/tuple of 4 video file names
        vid_output (optional) - name of the output file
        aud_file (optional) - full path to the audio file

    Returns:
        Output from ffmpeg
    """
    assert len(vid_files) == 4

    import ffmpeg

    vid_inputs = '" -i "'.join([''] + vid_files).removeprefix('" ') + '"'
    if vid_output is None:
        v0 = Path(vid_files[0])
        pre = str(v0.stem)
        vid_output = f'{os.path.join(v0.parent, pre)}-montage.mp4'
    aud_input_str = '' 
    aud_input = '0' # get audio from the first file if there is no audio
    aud_codec_str = ''
    if aud_file is not None:
        assert os.path.exists(aud_file)
        aud_input_str = f' -i "{aud_file}"'
        aud_input = '4'
        aud_codec_str = ' -c:a aac'

    ret = ''
    if (not os.path.exists(vid_output)) or overwrite:
        this_cmd = f'ffmpeg -y {vid_inputs}{aud_input_str} -filter_complex "[0:v][1:v][2:v][3:v]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[v]" -map "[v]" -map {aud_input}:a -c:v h264_nvenc{aud_codec_str} "{vid_output}"'
        ret = subprocess.getoutput(this_cmd)
    return ret

def download(url, start_time=None, end_time=None, dur=None, full_file=False):
    """
    Download a clip from a YouTube video.
    Downloads to the path specified in the module variable CLIP_FOLDER
    Returns the name of the downloaded video.
    """
    # url = 'https://www.youtube.com/watch?v=5umbf4ps0GQ'
    
    # download video
    for _ in range(3):
        try:
            yvid = YouTube(url)
            vid_found = True
            break
        except urllib.error.HTTPError:
            time.sleep(0.5)
            vid_found = False
    
    assert vid_found is True

    import ffmpeg

    ys = yvid.streams.get_highest_resolution()
    fname_in = ys.download(CLIP_FOLDER, url.split('?v=')[-1])
    if full_file: # otherwise clip the video
        return fname_in

    # specify interval
    if start_time is None: # manually find the start and stop times
        this_vid = View(fname_in)
        start_time = float(input('Specify start frame: '))/this_vid.fps
        end_time = float(input('Specify end frame: ')+1)/this_vid.fps
        dur = end_time - start_time
    else:
        assert isinstance(start_time, (float, int))
        assert not (end_time is None and dur is None)
        if dur is None:
            end_time = start_time + dur
        else:
            dur = end_time - start_time

    # clip video
    fname_out = os.path.join(CLIP_FOLDER, os.path.splitext(fname_in)[0] + '_s{:.3f}_e{:.3f}.mp4'.format(start_time, end_time))
    ffmpeg.input(fname_in, ss=start_time).output(fname_out, vcodec='h264_nvenc', t=dur).run()
    
    return fname_out
