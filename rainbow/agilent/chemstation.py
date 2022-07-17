import os 
import struct
from collections import deque
import numpy as np
from rainbow.datafile import DataFile
from rainbow.datadirectory import DataDirectory


"""
MAIN PARSING METHODS

"""

def parse_files(path):
    """
    Finds and parses Agilent Chemstation data files with a .ch, .uv, or .ms extension.

    Each successfully parsed file is stored as a DataFile.
    
    Args:
        path (str): Path to the Agilent .D data directory. 

    Returns:
        List containing a DataFile for each parsed data file. 

    """
    datafiles = []
    for file in os.listdir(path):
        datafile = parse_file(os.path.join(path, file))
        if datafile:
            datafiles.append(datafile)
    return datafiles

def parse_file(path):
    """
    Parses an Agilent Chemstation data file. Supported extensions are .ch, .uv, and .ms. 

    Calls the appropriate subroutine based on the file extension. 

    Args:
        path (str): Path to the Agilent data file.
    
    Returns:
        DataFile representing the file, if it can be parsed. Otherwise, None. 

    """
    ext = os.path.splitext(path)[1].lower()
    if ext == '.ch':
        return parse_ch(path)
    elif ext == '.uv':
        return parse_uv(path)
    elif ext == '.ms':
        return parse_ms(path)
    return None


"""
.ch PARSING METHODS

"""

def parse_ch(path):
    """
    Parses Agilent .ch files. 

    The .ch files containing FID data have a different file structure than other .ch files.

    This method determines the type of the .ch file using the file header, and calls the appropriate subroutine. 

    Args: 
        path (str): Path to the Agilent .ch file.
    
    Returns:
        DataFile representing the file, if it can be parsed. Otherwise, None.

    """
    f = open(path, 'rb')
    head = struct.unpack('>I', f.read(4))[0]
    f.close()

    if head == 0x03313739:
        return parse_ch_fid(path)
    elif head == 0x03313330:
        return parse_ch_other(path)
    return None

def parse_ch_fid(path):
    """
    Parses Agilent .ch files containing FID data. This method should not be called directly. Use parse_ch instead.

    The intervals between retention times (x-axis labels) are known to be constant, so the number of data points, first retention time, and last retention time are extracted from the file header and used to calculate every retention time. 

    Since the data values are stored in ascending order with respect to time, they are assigned to their corresponding retention times based on their order in the file.  

    More information about this file structure can be found :ref:`here <agilent_fid>`.

    Args:
        path (str): Path to the Agilent .ch file with FID data. 

    Returns:
        DataFile representing the file, if it can be parsed. Otherwise, None.

    """
    data_offsets = {
        'num_times': 0x116,
        'scaling_factor': 0x127C,
        'data_start': 0x1800
    }
    metadata_offsets = {
        'notebook': 0x35A, 
        'date': 0x957, 
        'method': 0xA0E, 
        'instrument': 0xC11, 
        'unit':  0x104C, 
        'signal': 0x1075 
    }
    
    f = open(path, 'rb')
    raw_bytes = f.read()

    # Extract the number of retention times.
    f.seek(data_offsets['num_times'])
    num_times = struct.unpack(">I", f.read(4))[0]
    if num_times == 0:
        return None
    
    # Calculate all retention times using the start and end times. 
    start_time = struct.unpack(">f", f.read(4))[0]
    end_time = struct.unpack(">f", f.read(4))[0]
    delta_time = (end_time - start_time) / (num_times - 1)
    times = np.arange(start_time, end_time + 1e-3, delta_time)
    assert (times.size == num_times)

    # Extract the raw data values.
    raw_matrix = np.ndarray(
        num_times, '<d', raw_bytes, data_offsets['data_start'], 8)
    raw_matrix = raw_matrix.copy().reshape(-1, 1)
    assert(raw_matrix.shape == (num_times, 1))

    # Extract the scaling factor. 
    f.seek(data_offsets['scaling_factor'])
    scaling_factor = struct.unpack('>d', f.read(8))[0]

    # Report time in minutes. 
    xlabels = times / 60000
    # No ylabel for FID data. 
    ylabels = np.array([''])
    # Apply scaling factor to raw values to get the real data.  
    data = scaling_factor * raw_matrix
    # Extract metadata from file header.
    metadata = read_header(f, metadata_offsets)

    f.close()

    return DataFile(path, 'FID', xlabels, ylabels, data, metadata)

def parse_ch_other(path):
    """
    Parses Agilent .ch files containing UV, CAD, or ELSD data. This method should not be called directly. Use parse_ch instead.  

    The entire file must be read to determine the total number of retention times (x-axis labels). But using a numpy array (with a fixed size of that number) would require reading the file a second time. It is faster to append elements to a python list than to read the file twice. This method uses a deque instead of a list, which is even faster.

    Since the intervals between retention times are known to be constant, the first retention time and last retention time are extracted from the file header and used to calculate every retention time.

    The wavelength (y-axis label) is extracted from the file header. 

    More information about this file structure can be found :ref:`here <agilent_uv_ch>`.

    Args:
        path (str): Path to the Agilent .ch file with UV, CAD, or ELSD data. 

    Returns:
        DataFile representing the file. 

    """
    data_offsets = {
        'time_range': 0x11A,
        'scaling_factor': 0x127C,
        'data_start': 0x1800
    }
    metadata_offsets = {
        'notebook': 0x35A, 
        'date': 0x957, 
        'method': 0xA0E, 
        'instrument': 0xC11, 
        'unit':  0x104C, 
        'signal': 0x1075 
    }

    f = open(path, 'rb')
    
    # Extract the raw data values.
    # Count the total number of retention times. 
    f.seek(data_offsets['data_start'])
    raw_array = deque()
    num_times = 0
    accum_absorbance = 0
    while True:
        # Parse segment header for the number of retention times.
        # If the segment header is invalid, stop reading.  
        head = struct.unpack('>B', f.read(1))[0] 
        seg_num_times = struct.unpack('>B', f.read(1))[0] 
        num_times += seg_num_times
        if head != 0x10:
            break
        # If the next value is an integer, reset the accumulator to that value.
        # Otherwise it is a delta, so add it to the accumulator. 
        for _ in range(seg_num_times):
            check_int = struct.unpack('>h', f.read(2))[0]
            if check_int == -0x8000:
                accum_absorbance = struct.unpack('>i', f.read(4))[0]
            else: 
                accum_absorbance += check_int
            raw_array.append(accum_absorbance)
    assert(f.tell() == os.path.getsize(path))

    # Calculate all retention times using the start and end times.
    f.seek(data_offsets['time_range'])
    start_time, end_time = struct.unpack('>II', f.read(8))
    delta_time = (end_time - start_time) / (num_times - 1)
    times = np.arange(start_time, end_time + 1, delta_time)

    # Extract metadata from file header.
    metadata = read_header(f, metadata_offsets)
    assert(metadata['unit'] == "mAU")

    # Determine the detector and signal using the metadata. 
    signal_str = metadata['signal']
    if '=' in signal_str:
        signal = int(float(signal_str.split('=')[1].split(',')[0]))
        detector = 'UV'
    elif 'ADC' in signal_str:
        signal = ''
        detector = 'CAD'
    assert('=' in signal_str or 'ADC' in signal_str)

    # Extract the scaling factor.
    f.seek(data_offsets['scaling_factor'])
    scaling_factor = struct.unpack('>d', f.read(8))[0]

    # Report time in minutes. 
    xlabels = times / 60000
    # No ylabel for CAD or ELSD data. 
    ylabels = np.array([signal])
    # Apply scaling factor to raw values to get the real data.  
    data = scaling_factor * np.array([raw_array]).transpose()

    f.close()

    return DataFile(path, detector, xlabels, ylabels, data, metadata)


"""
.uv PARSING METHODS

"""

def parse_uv(path):
    """
    Parses Agilent .uv files.

    More information about this file structure can be found :ref:`here <agilent_uv_uv>`.

    Args:
        path (str): Path to the Agilent .uv file. 
    
    Returns:
        DataFile representing the file, if it can be parsed. Otherwise, None.

    """
    data_offsets = {
        'num_times': 0x116,
        'scaling_factor': 0xC0D,
        'data_start': 0x1000
    }
    metadata_offsets = {
        "notebook": 0x35A,
        "date": 0x957,
        "method": 0xA0E,
        "unit": 0xC15,
        "datatype": 0xC40,
        "position": 0xFD7
    }

    f = open(path, 'rb')

    # Validate file header. 
    head = struct.unpack('>I', f.read(4))[0]
    if head != 0x03313331:
        f.close()
        return None

    # Extract the number of retention times.
    f.seek(data_offsets["num_times"])
    num_times = struct.unpack(">I", f.read(4))[0]
    # If there are none, the file may be a partial. 
    if not num_times:
        f.close()
        return parse_uv_partial(path)

    # Calculate all the wavelengths using the range from the first data segment header.
    f.seek(data_offsets["data_start"] + 0x8)
    start_wv, end_wv, delta_wv = tuple(num // 20 for num in struct.unpack("<HHH", f.read(6)))
    wavelengths = np.arange(start_wv, end_wv + 1, delta_wv)
    num_wavelengths = wavelengths.size

    # Extract the retention times and raw data values from each data segment.
    f.seek(data_offsets["data_start"])
    times = np.empty(num_times, np.uint32)
    raw_matrix = np.empty((num_times, num_wavelengths), np.int32)
    for i in range(num_times):
        # Parse segment header for the retention time.
        f.read(4)
        times[i] = struct.unpack("<I", f.read(4))[0]
        f.read(14)
        # If the next value is an integer, reset the accumulator to that value.
        # Otherwise it is a delta, so add it to the accumulator.
        accum_absorbance = 0 
        for j in range(num_wavelengths):
            check_int = struct.unpack('<h', f.read(2))[0]
            if check_int == -0x8000:
                accum_absorbance = struct.unpack('<i', f.read(4))[0]
            else: accum_absorbance += check_int
            raw_matrix[i, j] = accum_absorbance
    end_offset = f.tell()
    f.seek(0x104)
    assert(end_offset == struct.unpack('>I', f.read(4))[0])

    # Extract the scaling factor.
    f.seek(data_offsets['scaling_factor'])
    scaling_factor = struct.unpack('>d', f.read(8))[0]

    # Report time in minutes. 
    xlabels = times / 60000
    # For UV spectrum data, the ylabels are the wavelengths. 
    ylabels = wavelengths
    # Apply scaling factor to raw values to get the real data.  
    data = scaling_factor * raw_matrix
    # Extract metadata from file header.
    metadata = read_header(f, metadata_offsets)

    f.close()

    return DataFile(path, 'UV', xlabels, ylabels, data, metadata)

def parse_uv_partial(path):
    """
    A
    """
    data_offsets = {
        'num_times': 0x116,
        'scaling_factor': 0xC0D,
        'data_start': 0x1000
    }
    metadata_offsets = {
        "notebook": 0x35A,
        "date": 0x957,
        "method": 0xA0E,
        "unit": 0xC15,
        "datatype": 0xC40,
        "position": 0xFD7
    }

    f = open(path, 'rb')

    # Calculate all the wavelengths using the range from the first data segment header.
    # If there is no data, then it is not a valid partial file. 
    try:
        f.seek(data_offsets["data_start"] + 0x8)
        start_wv, end_wv, delta_wv = tuple(num // 20 for num in struct.unpack("<HHH", f.read(6)))
        wavelengths = np.arange(start_wv, end_wv + 1, delta_wv)
        num_wavelengths = wavelengths.size
    except struct.error:
        return None

    # Extract the retention times and raw data values from each data segment.
    f.seek(data_offsets['data_start'])
    memo = []
    while True:
        try:
            # Parse segment header for the retention time.
            f.read(4)
            time = struct.unpack("<I", f.read(4))[0]
            f.read(14)
            # If the next value is an integer, reset the accumulator to that value.
            # Otherwise it is a delta, so add it to the accumulator.
            raw_vals = np.empty(num_wavelengths, dtype=np.int32)
            accum_absorbance = 0 
            for j in range(num_wavelengths):
                check_int = struct.unpack('<h', f.read(2))[0]
                if check_int == -0x8000:
                    accum_absorbance = struct.unpack('<i', f.read(4))[0]
                else: accum_absorbance += check_int
                raw_vals[j] = accum_absorbance
            memo.append((time, raw_vals))
        except struct.error:
            break
    assert(f.tell() == os.path.getsize(path))

    # Organize the data using the number of retention times.
    num_times = len(memo)
    times = np.empty(num_times, dtype=np.uint32)
    raw_matrix = np.empty((num_times, num_wavelengths), dtype=np.int32)
    for i in range(num_times):
        time, raw_vals = memo[i]
        times[i] = time
        absorbances[i] = raw_vals

    # Extract the scaling factor.
    f.seek(data_offsets['scaling_factor'])
    scaling_factor = struct.unpack('>d', f.read(8))[0]

    # Report time in minutes. 
    xlabels = times / 60000
    # For UV spectrum data, the ylabels are the wavelengths. 
    ylabels = wavelengths
    # Apply scaling factor to raw values to get the real data.  
    data = scaling_factor * raw_matrix
    # Extract metadata from file header.
    metadata = read_header(f, metadata_offsets)

    f.close()

    return DataFile(path, 'UV', xlabels, ylabels, data, metadata)


"""
.ms PARSING METHODS

"""

def parse_ms(path):   
    """
    Parses Agilent .ms files.

    The type of .ms file is determined using the descriptive string at the start of the file. 

    Because the data segments for each retention time (x-axis label) contain data values for an arbitrary set of masses, the entire file must be read to determine the whole list of unique masses. To avoid rereading the file, the data is saved in a numpy matrix named memo as (mass, count) tuples.

    It turns out that checking membership in a python set is significantly faster than reading a value from a 2D numpy matrix. Accordingly, this method uses a set to populate the data array which increases speed by more than 3x.  

    More information about this file structure can be found :ref:`here <agilent_ms>`.

    Args:
        path (str): Path to Agilent .ms file. 
    
    Returns:
        DataFile representing the file, if it can be parsed. Otherwise, None.

    """
    data_offsets = {
        'type': 0x4,
        'data_start_pos': 0x10A,
        'lc_num_times': 0x116,
        'gc_num_times': 0x142
    }
    metadata_offsets = {
        'time': 0xB2,
        'method': 0xE4
    }

    f = open(path, 'rb')

    # Validate file header.
    # If invalid, the file may be a partial.
    head = struct.unpack('>I', f.read(4))[0]
    if head != 0x01320000:
        f.close()
        return parse_ms_partial(path)

    # Determine the type of .ms file based on header.
    # Read the number of retention times differently based on type.
    type_ms = read_string(f, data_offsets['type'], 1)
    if type_ms == "MSD Spectral File":
        f.seek(data_offsets['lc_num_times'])
        num_times = struct.unpack('>I', f.read(4))[0]
    else: 
        f.seek(data_offsets['gc_num_times'])
        num_times = struct.unpack('<I', f.read(4))[0]
    
    # Find the starting offset for the data.  
    f.seek(data_offsets['data_start_pos'])
    f.seek(struct.unpack('>H', f.read(2))[0] * 2 - 2)
    assert(type_ms != "MSD Spectral File" or f.tell() == 754)

    # Extract data values.
    times = np.empty(num_times, dtype=np.uint32)
    memo = np.empty(num_times, dtype=object)
    masses_set = set()
    for i in range(num_times):
        # Read in header information.
        cur = f.tell()
        length = struct.unpack('>H', f.read(2))[0] * 2
        times[i] = struct.unpack('>I', f.read(4))[0]
        f.read(6)
        num_masses = struct.unpack('>H', f.read(2))[0]
        f.read(4)
        
        if num_masses == 0:
            memo[i] = None
            f.read(10)
            continue

        # Process the data values. 
        data = struct.unpack('>' + num_masses * 'HH', f.read(num_masses * 4))
        masses = (np.array(data[::2]) + 10) // 20
        masses_set.update(masses)

        counts_enc = np.array(data[1::2])
        counts_head = counts_enc >> 14
        counts_tail = counts_enc & 0x3fff
        counts = (8 ** counts_head) * counts_tail

        memo[i] = (masses, counts)
        
        f.read(10)
        assert(cur + length == f.tell())

    masses_array = np.array(sorted(masses_set))
    mass_indices = dict(zip(masses_array, range(masses_array.size)))

    data_array = np.zeros((num_times, masses_array.size), dtype=int)
    for i in range(num_times):
        if not memo[i]:
            continue
        masses, counts = memo[i]
        visited = set()
        for j in range(masses.size):
            if masses[j] in visited:
                data_array[i, mass_indices[masses[j]]] += counts[j]
            else:
                data_array[i, mass_indices[masses[j]]] = counts[j]
                visited.add(masses[j])

    # print(path, hex(f.tell()))

    xlabels = times / 60000
    ylabels = masses_array 
    data = data_array
    metadata = read_header(f, metadata_offsets, 1)

    f.close()

    return DataFile(path, 'MS', xlabels, ylabels, data, metadata)

def parse_ms_partial(path):
    """
    A
    """
    f = open(path, 'rb')
    f.seek(0x10A)
    if struct.unpack('>H', f.read(2))[0] != 0:
        print(path)
        return None

    f.seek(754)
    memo = []
    masses_set = set()
    while True:
        try:
            cur = f.tell()
            length = struct.unpack('>H', f.read(2))[0] * 2
            time = struct.unpack('>I', f.read(4))[0]
            f.read(6)
            num_masses = struct.unpack('>H', f.read(2))[0]
            f.read(4)
        
            if num_masses == 0:
                memo.append((time, None, None))
                f.read(10)
                continue

            data = struct.unpack('>' + num_masses * 'HH', f.read(num_masses * 4))
            masses = (np.array(data[::2]) + 10) // 20
            masses_set.update(masses)

            counts_enc = np.array(data[1::2])
            counts_head = counts_enc >> 14
            counts_tail = counts_enc & 0x3fff
            counts = (8 ** counts_head) * counts_tail

            memo.append((time, masses, counts))
        
            f.read(10)
            assert(cur + length == f.tell())
        except struct.error:
            break

    num_times = len(memo)
    times = np.empty(num_times)

    masses_array = np.array(sorted(masses_set))
    mass_indices = dict(zip(masses_array, range(masses_array.size)))

    data_array = np.zeros((num_times, masses_array.size), dtype=int)
    for i in range(num_times):
        if not memo[i][1]:
            continue
        time, masses, counts = memo[i]
        times[i] = time
        visited = set()
        for j in range(masses.size):
            if masses[j] in visited:
                data_array[i, mass_indices[masses[j]]] += counts[j]
            else:
                data_array[i, mass_indices[masses[j]]] = counts[j]
                visited.add(masses[j])
    
    metadata_offsets = {
        'time': 0xB2,
        'method': 0xE4
    }

    xlabels = times / 60000
    ylabels = masses_array 
    data = data_array
    metadata = read_header(f, metadata_offsets, 1)

    f.close()

    return DataFile(path, 'MS', xlabels, ylabels, data, metadata)


"""
UTILITY METHODS

"""

def read_string(f, offset, gap=2):
    """
    Extracts a string from the specified offset.

    This method is primarily useful for retrieving metadata. 

    Args:
        f (_io.BufferedReader): File opened in 'rb' mode. 
        offset (int): Offset to begin reading from. 
        gap (int): Distance between two adjacent characters.
    
    Returns:
        String at the specified offset in the file header. 

    """
    f.seek(offset)
    str_len = struct.unpack("<B", f.read(1))[0] * gap
    return f.read(str_len)[::gap].decode()

def read_header(f, offsets, gap=2):
    """
    Extracts metadata from the header of an Agilent data file. 

    Args:
        f (_io.BufferedReader): File opened in 'rb' mode.
        offsets (dict): Dictionary mapping properties to file offsets. 
        gap (int): Distance between two adjacent characters.

    Returns:
        Dictionary containing metadata as string key-value pairs. 

    """
    metadata = {}
    for key, offset in offsets.items():
        string = read_string(f, offset, gap)
        if string:
            metadata[key] = string
    return metadata