"""initial

Revision ID: 0001
Revises: 
Create Date: 2026-04-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. sections
    op.create_table('sections',
        sa.Column('section_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('start_station_code', sa.String(), nullable=False),
        sa.Column('end_station_code', sa.String(), nullable=False),
        sa.Column('total_length_km', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('section_id')
    )
    # 2. trains
    op.create_table('trains',
        sa.Column('train_id', sa.String(), nullable=False),
        sa.Column('train_number', sa.String(), nullable=False),
        sa.Column('train_type', sa.String(), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('max_speed_kmph', sa.Float(), nullable=False),
        sa.Column('length_meters', sa.Float(), nullable=False),
        sa.Column('acceleration_mps2', sa.Float(), nullable=False),
        sa.Column('deceleration_mps2', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('train_id')
    )
    # 3. track_blocks
    op.create_table('track_blocks',
        sa.Column('block_id', sa.String(), nullable=False),
        sa.Column('section_id', sa.String(), nullable=False),
        sa.Column('start_km', sa.Float(), nullable=False),
        sa.Column('end_km', sa.Float(), nullable=False),
        sa.Column('length_km', sa.Float(), nullable=False),
        sa.Column('speed_limit_kmph', sa.Float(), nullable=False),
        sa.Column('is_electrified', sa.Boolean(), nullable=False),
        sa.Column('gradient', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('block_id'),
        sa.ForeignKeyConstraint(['section_id'], ['sections.section_id'], )
    )
    # 4. loop_stations
    op.create_table('loop_stations',
        sa.Column('station_id', sa.String(), nullable=False),
        sa.Column('section_id', sa.String(), nullable=False),
        sa.Column('station_code', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('location_km', sa.Float(), nullable=False),
        sa.Column('number_of_loops', sa.Integer(), nullable=False),
        sa.Column('loop_capacity_meters', sa.Float(), nullable=False),
        sa.Column('can_overtake', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('station_id'),
        sa.ForeignKeyConstraint(['section_id'], ['sections.section_id'], )
    )
    # 5. schedule_entries
    op.create_table('schedule_entries',
        sa.Column('entry_id', sa.String(), nullable=False),
        sa.Column('train_id', sa.String(), nullable=False),
        sa.Column('station_id', sa.String(), nullable=True),
        sa.Column('block_id', sa.String(), nullable=True),
        sa.Column('planned_arrival', sa.DateTime(), nullable=False),
        sa.Column('planned_departure', sa.DateTime(), nullable=False),
        sa.Column('actual_arrival', sa.DateTime(), nullable=True),
        sa.Column('actual_departure', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('delay_minutes', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('entry_id'),
        sa.ForeignKeyConstraint(['train_id'], ['trains.train_id'], ),
        sa.ForeignKeyConstraint(['station_id'], ['loop_stations.station_id'], ),
        sa.ForeignKeyConstraint(['block_id'], ['track_blocks.block_id'], )
    )
    # 6. optimization_runs
    op.create_table('optimization_runs',
        sa.Column('run_id', sa.String(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('solve_time_ms', sa.Float(), nullable=False),
        sa.Column('objective_value', sa.Float(), nullable=False),
        sa.Column('is_optimal', sa.Boolean(), nullable=False),
        sa.Column('conflicts_resolved', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('run_id')
    )
    # 7. controller_actions
    op.create_table('controller_actions',
        sa.Column('action_id', sa.String(), nullable=False),
        sa.Column('run_id', sa.String(), nullable=True),
        sa.Column('train_id', sa.String(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('action_type', sa.String(), nullable=False),
        sa.Column('reason', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('action_id'),
        sa.ForeignKeyConstraint(['run_id'], ['optimization_runs.run_id'], ),
        sa.ForeignKeyConstraint(['train_id'], ['trains.train_id'], )
    )
    # 8. conflict_events
    op.create_table('conflict_events',
        sa.Column('event_id', sa.String(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('conflict_type', sa.String(), nullable=False),
        sa.Column('train1_id', sa.String(), nullable=False),
        sa.Column('train2_id', sa.String(), nullable=True),
        sa.Column('block_id', sa.String(), nullable=True),
        sa.Column('severity', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('event_id'),
        sa.ForeignKeyConstraint(['train1_id'], ['trains.train_id'], ),
        sa.ForeignKeyConstraint(['train2_id'], ['trains.train_id'], ),
        sa.ForeignKeyConstraint(['block_id'], ['track_blocks.block_id'], )
    )
    # 9. kpi_snapshots
    op.create_table('kpi_snapshots',
        sa.Column('snapshot_id', sa.String(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('section_throughput', sa.Float(), nullable=False),
        sa.Column('avg_weighted_delay', sa.Float(), nullable=False),
        sa.Column('punctuality_index', sa.Float(), nullable=False),
        sa.Column('track_utilization', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('snapshot_id')
    )

def downgrade() -> None:
    op.drop_table('kpi_snapshots')
    op.drop_table('conflict_events')
    op.drop_table('controller_actions')
    op.drop_table('optimization_runs')
    op.drop_table('schedule_entries')
    op.drop_table('loop_stations')
    op.drop_table('track_blocks')
    op.drop_table('trains')
    op.drop_table('sections')
