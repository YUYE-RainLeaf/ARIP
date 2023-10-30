import gzip
from io import StringIO
from pathlib import Path

import pandas as pd
from pandas.api.types import is_numeric_dtype

from .PDB_constants import *
from .PDB_dihedral_angle import pdb_dihedral_angle
from .PDB_dotarray_volume import pdb_dotarray_volume
from .PDB_dotarray_surface import pdb_dotarray_surface
from .PDB_csv_sort import pdb_csv_sort
from .utils import timer_s, die
from .typing import *


def load_atom_models(name, fp:Path) -> List[Tuple[int, AtomModel]]:
    # Determine if it is a compressed file
    is_zip = False
    with open(fp, 'rb') as f:
        header = f.read(10)
        is_zip = header.startswith(b'\x1f\x8b\x08') # gzip file header
    
    if is_zip: open_file = lambda fp: gzip.open(fp, 'rt')
    else:      open_file = lambda fp:      open(fp, 'r')
    try:
        with open_file(fp) as fh:
            line = fh.readline()
            lines: List[str] = fh.readlines()
    except:
        die(f'The file {name} is unsupported format')

    MODEL  = [i for i in range(len(lines)) if lines[i].startswith('MODEL')]
    ENDMDL = [i for i in range(len(lines)) if lines[i].startswith('ENDMDL')]
    
    if not MODEL and not ENDMDL:                # Only one MODEL
        MDL = [line for line in lines if line.startswith('ATOM') or line.startswith('HETATM')]
    elif MODEL and len(MODEL) == len(ENDMDL):   # Multiple MODEL
        MDL = {i+1: lines[MODEL[i] : ENDMDL[i]] for i in range(len(MODEL))}
    else:
        die(f'The file {name} may be corrupted or contain incomplete MODEL')
    
    a_models = []
    
    if type(MDL) == list:
        idx = -1 # Indicates only one MODEL
        # Remove hydrogen atoms, terminal oxygen atoms, and multiple atoms in the same position
        a_model = [
            line for line in MDL if
                'OXT' not in line
                    and (line[16] == ' ' or line[16] == 'A')
                    and line[76:78].lstrip() != 'H' # The element symbol only has two positions, so use lstrip()
                    and line[17:20] != 'HOH' # No water
        ]
        a_models.append((idx, a_model))

    elif type(MDL) == dict:
        for idx in MDL: # Here idx is not -1, indicating more than one MODEL
            # Only those starting with ATOM are standard residue atoms
            a_model = [
                line for line in MDL[idx] if
                    (line.startswith('ATOM') or line.startswith('HETATM')) # Must add parentheses, otherwise it will affect the subsequent parallel condition judgment
                        and 'OXT' not in line
                        and (line[16] == ' ' or line[16] == 'A')
                        and line[76:78].lstrip() != 'H'
                        and line[17:20] != 'HOH'
            ]
            a_models.append((idx, a_model))
    
    return a_models


def parse_atom_model(atom_model:AtomModel, disable_print=False) -> DataFrame:
    # Create a dictionary to convert to DataFrame
    atom_info = {
        'Chain':   [],      # str
        'ResName': [],      # str
        'Atom':    [],      # str
        'x':       [],      # float
        'y':       [],      # float
        'z':       [],      # float
        'R':       [],      # float
        'Type':    [],      # str
        'Surf':    [],      # float
        'Volu':    [],      # float
    }

    for atom in atom_model:
        Res = atom[17:20].lstrip()  # Residue name
        Num = str(int(atom[22:26])) # Residue number
        Ana = atom[12:16].strip()   # Atom name
        Ele = atom[76:78].lstrip()  # Element type
        
        # Residue, atom, coordinates, radius, type, surface, volume
        atom_info['Chain']  .append(atom[21] + Num)
        atom_info['ResName'].append(Res)
        atom_info['Atom']   .append(Ana)
        atom_info['x']      .append(float(atom[30:38]))
        atom_info['y']      .append(float(atom[38:46]))
        atom_info['z']      .append(float(atom[46:54]))
        
        if Res in Radius: # Standard residue
            if Res in AA and Ana in Radius[Res]: # Amino acid
                __ = Radius[Res][Ana]
                atom_info['R']   .append(__[0])
                atom_info['Type'].append(__[1])
                atom_info['Surf'].append(__[2])
                atom_info['Volu'].append(__[3])
            if Res in NT and Ana[0] in Radius[Res]: # Nucleotide
                __ = Radius[Res][Ele]
                atom_info['R']   .append(__[0])
                atom_info['Type'].append(__[1])
                atom_info['Surf'].append(__[2])
                atom_info['Volu'].append(__[3])
        
        # Non-standard residue
        else:
            __ = Radius['UNDEF'][Ele]
            atom_info['R']   .append(__[0])
            atom_info['Type'].append(__[1])
            atom_info['Surf'].append(__[2])
            atom_info['Volu'].append(__[3])   

    atom_df = pd.DataFrame.from_dict(atom_info)

    # NOTE: Add the radius of the water molecule
    atom_df['R'] += Radius_H2O
    
    for col in atom_df.columns:
        if is_numeric_dtype(atom_df[col]): # Convert all values to np.float64 type
            atom_df[col] = atom_df[col].astype(DTYPE, copy=False)
    
    # Extract rows in the ResName column in the AA or NA dictionary
    aa = atom_df[atom_df['ResName'].isin(AA)]
    nt = atom_df[atom_df['ResName'].isin(NT)]
    ns = atom_df[~atom_df['ResName'].isin(AA) & ~atom_df['ResName'].isin(NT)]
    
    # Proteins use single-letter abbreviations, nucleic acids use two or three-letter abbreviations
    aa_tmp = aa.copy()
    nt_tmp = nt.copy()
    ns_tmp = ns.copy()
    aa_tmp['ResName'] = aa_tmp['ResName'].map(AA)
    nt_tmp['ResName'] = nt_tmp['ResName'].map(NT)
    
    # Merge Chain and ResName columns
    aa_tmp['Residue'] = aa_tmp['Chain'] + '-' + aa_tmp['ResName']
    nt_tmp['Residue'] = nt_tmp['Chain'] + '-' + nt_tmp['ResName']
    ns_tmp['Residue'] = ns_tmp['Chain'] + ';' + ns_tmp['ResName'] # Use a different connector for easy replacement
    
    aa_df = aa_tmp[['Residue', 'Atom', 'x', 'y', 'z', 'R', 'Type', 'Surf', 'Volu']]
    nt_df = nt_tmp[['Residue', 'Atom', 'x', 'y', 'z', 'R', 'Type', 'Surf', 'Volu']]
    ns_df = ns_tmp[['Residue', 'Atom', 'x', 'y', 'z', 'R', 'Type', 'Surf', 'Volu']]
    
    if not disable_print:
        # If not empty, display
        if not aa.empty:
            print(aa_df)
            print('len(amino_acid_df):', len(aa))
        if not nt.empty:
            print(nt_df)
            print('len(nucleotide_df):', len(nt))
        if not ns.empty:
            print(ns_df)
            print('len(non_standard_df):', len(ns))
        
    atom_df = [aa_df, nt_df, ns_df]
    
    return atom_df


@timer_s
def arip_analyze(idx:int, name:str, atom_model:List[str], interval:float, ref_fp:Path, out_dp:Path, threshold:List[float], density:bool, compress:bool, disable_print=False):
    # Load data
    aa_df, nt_df, ns_df = parse_atom_model(atom_model, disable_print)
    
    pdb_file = StringIO(''.join(atom_model))
    # Calculate dihedral angles only for protein
    dihedral_angle = False
    if not aa_df.empty:
        dihedral_angle = pdb_dihedral_angle(name, pdb_file, disable_print)
    
    atom_df = pd.concat([aa_df, nt_df, ns_df], axis=0)
    
    # Different precision, different number of points. Calculate contact volume
    contact_dict, volume = pdb_dotarray_volume(atom_df, density, interval, disable_print)
    
    # If memory is insufficient, skip the current file
    if not contact_dict and not volume:
        print(f'Skipping file {name} due to insufficient memory or no valid atoms')
        return -1, -1 # Indicates memory shortage error
    
    # Delete unnecessary columns
    contact_df = atom_df[['Name', 'x', 'y', 'z', 'R', 'Surf', 'Type']]
    
    # Calculate the distance and contact surface between atom pairs
    surface = pdb_dotarray_surface(ref_fp, contact_df, contact_dict, disable_print)
    
    # Organize data and determine contact type
    pdb_csv_sort(idx, name, dihedral_angle, surface, volume, out_dp, threshold, compress, disable_print)

def arip_main(in_fp:Path, out_dp:Path, ref_fp:Path, interval:float, threshold:List[float], density:bool, compress:bool, disable_print=False):
    name = Path(in_fp.stem).stem # Use stem twice because the compressed file needs to remove the .pdb suffix again
    a_models = load_atom_models(name, in_fp)
    
    try:
        for idx, a_model in a_models:
            if a_model == []: # No valid atoms
                if idx == -1: print(f'Skipping file {name} due to no valid atoms')
                else:         print(f'Skipping file {name}_MODEL_{idx} due to no valid atoms')
                
            else:
                t = arip_analyze(idx, name, a_model, interval, ref_fp, out_dp, threshold, density, compress, disable_print)
                
                if isinstance(t, float): # If there is a memory shortage error, then t is a tuple containing 3 elements, the last two are -1. If there is no error, t is a floating point number
                    if idx == -1: print(f'The PDB {name} run OK, time cost: {t:.3f}s')
                    else:         print(f'The PDB {name}_MODEL_{idx} run OK, time cost: {t:.3f}s')
    except:
        print(f'The file {name} cannot be analyzed, perhaps it contains unsupported format, or no valid atoms')

