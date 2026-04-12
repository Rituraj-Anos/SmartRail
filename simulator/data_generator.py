import random
from typing import List
from core.models.train import Train, TrainType, Priority
from core.models.section import Section, LoopStation, TrackBlock

def generate_mumbai_pune_section() -> Section:
    """Generates a synthetic 20-station section for Mumbai-Pune."""
    stations = []
    blocks = []
    
    # Approx 150 km distance, 20 stations
    total_length = 150.0
    num_stations = 20
    avg_block_length = total_length / (num_stations - 1)
    
    current_km = 0.0
    for i in range(num_stations):
        station_code = f"STN{i:02d}"
        if i == 0:
            name = "Mumbai_CSMT"
        elif i == num_stations - 1:
            name = "Pune_JN"
        else:
            name = f"Station_{i}"
            
        stations.append(LoopStation(
            station_id=f"stn_{i}",
            station_code=station_code,
            name=name,
            location_km=current_km,
            number_of_loops=random.choice([2, 3, 4]),
            loop_capacity_meters=700.0,
            can_overtake=True
        ))
        
        if i < num_stations - 1:
            next_km = current_km + avg_block_length + random.uniform(-1, 1) # slight variation
            blocks.append(TrackBlock(
                block_id=f"blk_{i}",
                start_km=current_km,
                end_km=next_km,
                length_km=next_km - current_km,
                speed_limit_kmph=random.choice([80.0, 100.0, 110.0])
            ))
            current_km = next_km
            
    section = Section(
        section_id="sec_mum_pun",
        name="Mumbai - Pune",
        start_station_code=stations[0].station_code,
        end_station_code=stations[-1].station_code,
        total_length_km=current_km,
        blocks=blocks,
        stations=stations,
        signals=[] # omitting signals for now
    )
    return section

def generate_scenario(num_stations: int, num_trains: int):
    section = generate_mumbai_pune_section()
    # Mocking num_stations is somewhat hard without rebuilding, so we just use the existing logic
    # but strictly respect num_trains
    if num_stations != len(section.stations):
        # Slice section if needed
        section.stations = section.stations[:num_stations]
        section.blocks = section.blocks[:num_stations-1]
        section.end_station_code = section.stations[-1].station_code
    trains = generate_trains(num_trains)
    return section, trains

def generate_trains(num_trains: int = 30) -> List[Train]:
    """Generates 30 realistic trains."""
    trains = []
    for i in range(num_trains):
        t_type = random.choice(list(TrainType))
        if t_type in [TrainType.VANDE_BHARAT, TrainType.RAJDHANI, TrainType.SHATABDI]:
            priority = Priority.CRITICAL
            speed = 130.0
        elif t_type == TrainType.EXPRESS:
            priority = Priority.HIGH
            speed = 110.0
        elif t_type == TrainType.PASSENGER:
            priority = Priority.MEDIUM
            speed = 80.0
        else:
            priority = Priority.LOW
            speed = 65.0
            
        trains.append(Train(
            train_id=f"trn_{i}",
            train_number=f"12{i:03d}",
            train_type=t_type,
            priority=priority,
            max_speed_kmph=speed,
            length_meters=random.choice([400.0, 600.0])
        ))
    return trains

def generate_timetable():
    section = generate_mumbai_pune_section()
    trains = generate_trains(30)
    
    print(f"Generated section: {section.name} with {len(section.stations)} stations.")
    print(f"Generated {len(trains)} trains.")
    # In a full generator, we'd assign ScheduleEntries crossing each station here.
    return section, trains

if __name__ == "__main__":
    generate_timetable()
