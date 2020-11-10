#!python

# builtin
import os
import sys
import contextlib
import logging
# external
import numpy as np
# local
import alphatims.utils

if sys.platform[:5] == "win32":
    BRUKER_DLL_FILE_NAME = "timsdata.dll"
elif sys.platform[:5] == "linux":
    BRUKER_DLL_FILE_NAME = "timsdata.so"
BRUKER_DLL_FILE_NAME = os.path.join(
    alphatims.utils.EXT_PATH,
    BRUKER_DLL_FILE_NAME
)
MSMSTYPE_PASEF = 8
MSMSTYPE_DIAPASEF = 9


def init_bruker_dll(bruker_dll_file_name):
    import ctypes
    logging.info(f"Reading bruker dll file {bruker_dll_file_name}")
    bruker_dll = ctypes.cdll.LoadLibrary(
        os.path.realpath(bruker_dll_file_name)
    )
    bruker_dll.tims_open.argtypes = [ctypes.c_char_p, ctypes.c_uint32]
    bruker_dll.tims_open.restype = ctypes.c_uint64
    bruker_dll.tims_close.argtypes = [ctypes.c_uint64]
    bruker_dll.tims_close.restype = None
    # dia_pasef_dll.tims_has_recalibrated_state.argtypes = [ctypes.c_uint64 ]
    # dia_pasef_dll.tims_has_recalibrated_state.restype = ctypes.c_uint32
    bruker_dll.tims_read_scans_v2.argtypes = [
        ctypes.c_uint64,
        ctypes.c_int64,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32
    ]
    bruker_dll.tims_read_scans_v2.restype = ctypes.c_uint32
    bruker_dll.tims_index_to_mz.argtypes = [
        ctypes.c_uint64,
        ctypes.c_int64,
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.c_uint32
    ]
    bruker_dll.tims_index_to_mz.restype = ctypes.c_uint32
    bruker_dll.tims_scannum_to_oneoverk0.argtypes = [
        ctypes.c_uint64,
        ctypes.c_int64,
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.c_uint32
    ]
    bruker_dll.tims_scannum_to_oneoverk0.restype = ctypes.c_uint32
    return bruker_dll


@contextlib.contextmanager
def open_bruker_d_folder(bruker_dll, bruker_d_folder_name):
    try:
        if isinstance(bruker_dll, str):
            bruker_dll = init_bruker_dll(bruker_dll)
        logging.info(f"Opening handle for {bruker_d_folder_name}")
        bruker_d_folder_handle = bruker_dll.tims_open(
            bruker_d_folder_name.encode('utf-8'),
            0
        )
        yield bruker_dll, bruker_d_folder_handle
    finally:
        logging.info(f"Closing handle for {bruker_d_folder_name}")
        bruker_dll.tims_close(bruker_d_folder_handle)


def read_bruker_frames(bruker_d_folder_name):
    import sqlite3
    import pandas as pd
    logging.info(f"Reading frames for {bruker_d_folder_name}")
    with sqlite3.connect(
        os.path.join(bruker_d_folder_name, "analysis.tdf")
    ) as sql_database_connection:
        global_meta_data = pd.read_sql_query(
            "SELECT * from GlobalMetaData",
            sql_database_connection
        )
        frames = pd.read_sql_query(
            "SELECT * FROM Frames",
            sql_database_connection
        )
        if MSMSTYPE_DIAPASEF in frames.MsMsType.values:
            acquisition_mode = "diaPASEF"
            fragment_frames = pd.read_sql_query(
                "SELECT * FROM DiaFrameMsMsInfo",
                sql_database_connection
            )
            fragment_frame_groups = pd.read_sql_query(
                "SELECT * from DiaFrameMsMsWindows",
                sql_database_connection
            )
            fragment_frames = fragment_frames.merge(
                fragment_frame_groups,
                how="left"
            )
        elif MSMSTYPE_PASEF in frames.MsMsType.values:
            acquisition_mode = "PASEF"
            fragment_frames = pd.read_sql_query(
                "SELECT * from PasefFrameMsMsInfo",
                sql_database_connection
            )
        else:
            raise ValueError("Scan mode is not PASEF or diaPASEF")
        return (
            acquisition_mode,
            global_meta_data,
            frames,
            fragment_frames,
        )


@alphatims.utils.njit(nogil=True)
def parse_frame_buffer(
    frame_id,
    buffer,
    scan_count,
    peak_start,
    scan_indptr,
    mz_indices,
    intensities,
):
    scan_offset = scan_count * (frame_id - 1)
    scan_indptr[scan_offset: scan_offset + scan_count] = buffer[:scan_count]
    end_indices = np.cumsum(buffer[:scan_count])
    for end, size in zip(end_indices, buffer[:scan_count]):
        start = end - size
        mz_start = scan_count + start * 2
        int_start = scan_count + start * 2 + size
        buffer_start = peak_start + start
        buffer_end = peak_start + end
        mz_indices[buffer_start: buffer_end] = buffer[
            mz_start: mz_start + size
        ]
        intensities[buffer_start: buffer_end] = buffer[
            int_start: int_start + size
        ]


def read_scans_of_frame(
    frame_id,
    frames,
    frame_indptr,
    intensities,
    mz_indices,
    scan_indptr,
    bruker_dll,
    bruker_d_folder_handle,
    calibrated_mzs=None,
    calibrated_ccs=None,
):
    import ctypes
    scan_start = 0
    scan_end = frames.NumScans[frame_id - 1]
    scan_count = scan_end - scan_start
    peak_start = frame_indptr[frame_id - 1]
    peak_end = frame_indptr[frame_id]
    peak_count = peak_end - peak_start
    buffer = np.empty(
        shape=scan_count + 2 * peak_count,
        dtype=np.uint32
    )
    bruker_dll.tims_read_scans_v2(
        bruker_d_folder_handle,
        frame_id,
        scan_start,
        scan_end,
        buffer.ctypes.data_as(ctypes.POINTER(ctypes.c_uint32)),
        len(buffer) * 4
    )
    parse_frame_buffer(
        frame_id,
        buffer,
        scan_count,
        peak_start,
        scan_indptr,
        mz_indices,
        intensities,
    )
    if calibrated_mzs is not None:
        bruker_dll.tims_index_to_mz(
            bruker_d_folder_handle,
            frame_id,
            mz_indices[peak_start: peak_end].astype(np.float64).ctypes.data_as(
                ctypes.POINTER(ctypes.c_double)
            ),
            calibrated_mzs[peak_start: peak_end].ctypes.data_as(
                ctypes.POINTER(ctypes.c_double)
            ),
            peak_count
        )
    if calibrated_ccs is not None:
        scan_offset = scan_count * (frame_id - 1)
        bruker_dll.tims_scannum_to_oneoverk0(
            bruker_d_folder_handle,
            frame_id,
            np.arange(scan_end, dtype=np.float64).ctypes.data_as(
                ctypes.POINTER(ctypes.c_double)
            ),
            calibrated_ccs[scan_offset: scan_offset + scan_count].ctypes.data_as(
                ctypes.POINTER(ctypes.c_double)
            ),
            scan_count
        )


def read_bruker_scans(
    frames,
    bruker_d_folder_name:str,
    bruker_calibrated_mz_values:bool=False,
    bruker_calibrated_mobility_values:bool=False,
):
    frame_indptr = np.empty(frames.shape[0] + 1, dtype=np.int64)
    frame_indptr[0] = 0
    frame_indptr[1:] = np.cumsum(frames.NumPeaks.values)
    scan_count = frames.NumScans.max() * frames.shape[0]
    scan_indptr = np.empty(
        scan_count + 1,
        dtype=np.int64
    )
    if bruker_calibrated_mz_values:
        calibrated_mzs = np.empty(frame_indptr[-1], dtype=np.float64)
    else:
        calibrated_mzs = None
    if bruker_calibrated_mobility_values:
        calibrated_ccs = np.empty(scan_count, dtype=np.float64)
    else:
        calibrated_ccs = None
    intensities = np.empty(frame_indptr[-1], dtype=np.uint16)
    tof_indices = np.empty(frame_indptr[-1], dtype=np.uint32)
    with open_bruker_d_folder(
        BRUKER_DLL_FILE_NAME,
        bruker_d_folder_name
    ) as (bruker_dll, bruker_d_folder_handle):
        logging.info(f"Reading scans for {bruker_d_folder_name}")
        for frame_id in range(1, frame_indptr.shape[0]):
            read_scans_of_frame(
                frame_id,
                frames,
                frame_indptr,
                intensities,
                tof_indices,
                scan_indptr,
                bruker_dll,
                bruker_d_folder_handle,
                calibrated_mzs=calibrated_mzs,
                calibrated_ccs=calibrated_ccs,
            )
    scan_indptr[1:] = np.cumsum(scan_indptr[:-1])
    scan_indptr[0] = 0
    return (
        scan_indptr,
        tof_indices,
        intensities,
        calibrated_mzs,
        calibrated_ccs,
    )


class TimsTOF(object):

    def __init__(
        self,
        bruker_d_folder_name:str,
        bruker_calibrated_mz_values:bool=False,
        bruker_calibrated_mobility_values:bool=False,
    ):
        logging.info(f"Importing data for {bruker_d_folder_name}")
        (
            self.acquisition_mode,
            global_meta_data,
            self.frames,
            self.fragment_frames,
        ) = read_bruker_frames(bruker_d_folder_name)
        (
            self.tof_indptr,
            self.tof_indices,
            self.intensities,
            calibrated_mz_values,
            calibrated_mobility_values,
        ) = read_bruker_scans(
            self.frames,
            bruker_d_folder_name,
            bruker_calibrated_mz_values,
            bruker_calibrated_mobility_values,
        )
        if bruker_calibrated_mz_values:
            self.calibrated_mz_values = calibrated_mz_values
        if bruker_calibrated_mobility_values:
            self.calibrated_mobility_values = calibrated_mobility_values
        self.meta_data = dict(
            zip(global_meta_data.Key, global_meta_data.Value)
        )
        self.frame_max_index = self.frames.shape[0]
        self.scan_max_index = self.frames.NumScans.max()
        self.tof_max_index = int(self.meta_data["DigitizerNumSamples"])
        self.rt_values = self.frames.Time.values
        self.mobility_min_value = float(
            self.meta_data["OneOverK0AcqRangeLower"]
        )
        self.mobility_max_value = float(
            self.meta_data["OneOverK0AcqRangeUpper"]
        )
        self.mobility_values = self.mobility_max_value - (
            self.mobility_max_value - self.mobility_min_value
        ) / self.scan_max_index * np.arange(self.scan_max_index)
        self.mz_min_value = float(self.meta_data["MzAcqRangeLower"])
        self.mz_max_value = float(self.meta_data["MzAcqRangeUpper"])
        self.tof_intercept = np.sqrt(self.mz_min_value)
        self.tof_slope = (
            np.sqrt(self.mz_max_value) - self.tof_intercept
        ) / self.tof_max_index
        self.mz_values = (
            self.tof_intercept + self.tof_slope * np.arange(self.tof_max_index)
        )**2
        (
            self.quad_indptr,
            self.quad_low_values,
            self.quad_high_values,
        ) = parse_quad_indptr(
            self.fragment_frames.Frame.values,
            self.fragment_frames.ScanNumBegin.values,
            self.fragment_frames.ScanNumEnd.values,
            self.fragment_frames.IsolationMz.values,
            self.fragment_frames.IsolationWidth.values,
            self.scan_max_index,
            self.frame_max_index,
        )

    def convert_from_indices(
        self,
        raw_indices=None,
        *,
        frame_indices=None,
        scan_indices=None,
        tof_indices=None,
        to_frame_indices:bool=False,
        to_scan_indices:bool=False,
        to_tof_indices:bool=False,
        to_rt_values:bool=False,
        to_mobility_values:bool=False,
        to_mz_values:bool=False,
        return_as_dict:bool=False,
    ):
        values = {}
        if (raw_indices is not None) and any(
            [
                to_frame_indices,
                to_scan_indices,
                to_rt_values,
                to_mobility_values,
            ]
        ):
            parsed_indices = np.searchsorted(
                self.tof_indptr,
                raw_indices,
                "right"
            ) - 1
        if (to_frame_indices or to_rt_values) and frame_indices is None:
            frame_indices = parsed_indices // self.scan_max_index
        if (to_scan_indices or to_mobility_values) and scan_indices is None:
            scan_indices = parsed_indices % self.scan_max_index
        if (to_tof_indices or to_mz_values) and tof_indices is None:
            tof_indices = self.tof_indices[raw_indices]
        if to_frame_indices:
            values["frame_indices"] = frame_indices
        if to_scan_indices:
            values["scan_indices"] = scan_indices
        if to_tof_indices:
            values["tof_indices"] = tof_indices
        if to_rt_values:
            values["rt_values"] = self.rt_values[frame_indices]
        if to_mobility_values:
            values["mobility_values"] = self.mobility_values[scan_indices]
        if to_mz_values:
            values["mz_values"] = self.mz_values[tof_indices]
        if not return_as_dict:
            # python >= 3.7 maintains dict insertion order
            return list(values.values())
        else:
            return values

    def convert_to_indices(
        self,
        values,
        *,
        to_frame_indices:bool=False,
        to_scan_indices:bool=False,
        to_tof_indices:bool=False,
        side:str="right"
    ):
        if side not in ("left", "right"):
            raise KeyError("Invalid side")
        if to_frame_indices:
            indices = np.searchsorted(
                self.rt_values,
                values,
                side=side
            )
        elif to_scan_indices:
            indices = self.scan_max_index / (
                self.mobility_max_value - self.mobility_min_value
            ) * (values - self.mobility_min_value)
        elif to_tof_indices:
            indices = (np.sqrt(values) - self.tof_intercept) / self.tof_slope
        else:
            raise KeyError("No target index defined")
        if not to_frame_indices:
            if side == "left":
                indices = np.floor(indices).astype(np.int32)
            elif side == "right":
                indices = np.ceil(indices).astype(np.int32)
        return indices

    def __getitem__(self, keys):
        stretch_starts = self.tof_indptr[:-1].reshape(
            self.frame_max_index,
            self.scan_max_index
        )
        stretch_ends = self.tof_indptr[1:].reshape(
            self.frame_max_index,
            self.scan_max_index
        )
#         try:
#             key_iter = iter(keys)
#         except TypeError:
#             if isinstance(keys, float):
#                 keys = np.searchsorted(
#                     self.rt_values,
#                     keys
#                 )
#             if isinstance(keys, int):
#                 return np.arange(
#                     self.tof_indptr[keys * self.scan_max_index],
#                     self.tof_indptr[(keys + 1) * self.scan_max_index],
#                 )
#             stretch_start = stretch_starts[keys]
#             stretch_end = stretch_starts[keys]
#             return
#         while len(keys) < 3:
#             keys.append(slice(None))
        if len(keys) != 3:
            raise IndexError("Slice triple expected")
        new_keys = []
        for i, key in enumerate(keys):
            if isinstance(key, slice):
                slice_start = key.start
                slice_stop = key.stop
                slice_step = key.step
                if isinstance(slice_start, float):
                    slice_start = self.convert_to_indices(
                        slice_start,
                        to_frame_indices=(i == 0),
                        to_scan_indices=(i == 1),
                        to_tof_indices=(i == 2),
                    )
                if isinstance(slice_stop, float):
                    slice_stop = self.convert_to_indices(
                        slice_stop,
                        to_frame_indices=(i == 0),
                        to_scan_indices=(i == 1),
                        to_tof_indices=(i == 2),
                        side="right"
                    )
                new_keys.append(slice(slice_start, slice_stop, slice_step))
        keys = tuple(new_keys)
        slice_start = keys[2].start
        if slice_start is None:
            slice_start = -np.inf
        slice_stop = keys[2].stop
        if slice_stop is None:
            slice_stop = np.inf
        slice_step = keys[2].step
        if slice_step is None:
            slice_step = 1
        mask = sparse_slice(
            self.tof_indices,
            slice_start,
            slice_stop,
            slice_step,
            stretch_starts[keys[:2]].flatten(),
            stretch_ends[keys[:2]].flatten(),
        )
        return mask

    def bin_intensities(self, indices, axis):
        intensities = self.intensities[indices].astype(np.float64)
        max_index = {
            "rt": self.frame_max_index,
            "mobility": self.scan_max_index,
            "mz": self.tof_max_index,
        }
        parsed_indices = self.convert_from_indices(
            indices,
            to_frame_indices="rt" in axis,
            to_scan_indices="mobility" in axis,
            to_tof_indices="mz" in axis,
            return_as_dict=True,
        )
        binned_intensities = np.zeros(tuple([max_index[ax] for ax in axis]))
        parse_dict = {
            "rt": "frame_indices",
            "mobility": "scan_indices",
            "mz": "tof_indices",
        }
        bin_intensities(
            range(indices.size),
            intensities,
            tuple(
                [
                    parsed_indices[parse_dict[ax]] for ax in axis
                ]
            ),
            binned_intensities
        )
        return binned_intensities


@alphatims.utils.njit
def sparse_slice(
    index_array,
    slice_start,
    slice_stop,
    slice_step,
    sparse_starts,
    sparse_ends,
):
    result = []
    for sparse_start, sparse_end in zip(sparse_starts, sparse_ends):
        if (
            sparse_start == sparse_end
        ) or (
            index_array[sparse_end - 1] < slice_start
        ) or (
            index_array[sparse_start] > slice_stop
        ):
            continue
        if slice_start == -np.inf:
            idx_start = sparse_start
        else:
            idx_start = sparse_start + np.searchsorted(
                index_array[sparse_start: sparse_end],
                slice_start,
                "left"
            )
        if slice_stop == np.inf:
            idx_stop = sparse_end
        else:
            idx_stop = idx_start + np.searchsorted(
                index_array[idx_start: sparse_end],
                slice_stop,
                "left"
            )
        if slice_step == 1:
            for idx in range(idx_start, idx_stop):
                result.append(idx)
        else:
            for idx in range(idx_start, idx_stop):
                if ((index_array[idx] - slice_start) % slice_step) == 0:
                    result.append(idx)
    return np.array(result)


@alphatims.utils.njit
def parse_quad_indptr(
    frame_ids,
    scan_begins,
    scan_ends,
    isolation_mzs,
    isolation_widths,
    scan_max_index,
    frame_max_index,
):
    quad_indptr = [0]
    quad_low_values = []
    quad_high_values = []
    high = -1
    for (
        frame_id,
        scan_begin,
        scan_end,
        isolation_mz,
        isolation_width,
    ) in zip(
        frame_ids - 1,
        scan_begins,
        scan_ends,
        isolation_mzs,
        isolation_widths / 2,
    ):
        low = frame_id * scan_max_index + scan_begin - 1
        if low > high:
            quad_indptr.append(low)
            quad_low_values.append(-1)
            quad_high_values.append(-1)
        high = frame_id * scan_max_index + scan_end
        quad_indptr.append(high)
        quad_low_values.append(isolation_mz - isolation_width)
        quad_high_values.append(isolation_mz + isolation_width)
    quad_max_index = scan_max_index * frame_max_index
    if high < quad_max_index:
        quad_indptr.append(quad_max_index)
        quad_low_values.append(-1)
        quad_high_values.append(-1)
    return (
        np.array(quad_indptr),
        np.array(quad_low_values),
        np.array(quad_high_values),
    )


# Overhead of using multiple threads is slower
@alphatims.utils.pjit(thread_count=1)
def bin_intensities(
    query,
    intensities,
    parsed_indices,
    intensity_bins
):
    intensity = intensities[query]
    if len(parsed_indices) == 1:
        intensity_bins[parsed_indices[0][query]] += intensity
    elif len(parsed_indices) == 2:
        intensity_bins[
            parsed_indices[0][query],
            parsed_indices[1][query]
        ] += intensity
