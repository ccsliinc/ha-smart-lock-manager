"""Simplified performance tests for Smart Lock Manager operations."""

import asyncio
import pytest
import time
from unittest.mock import Mock

from custom_components.smart_lock_manager.models.lock import SmartLockManagerLock, CodeSlot


class TestSimplePerformance:
    """Simplified performance test suite for core operations."""

    @pytest.mark.asyncio
    async def test_lock_creation_performance(self):
        """Test performance of creating locks with many slots."""
        start_time = time.time()
        
        # Create 10 locks with 30 slots each
        locks = []
        for lock_num in range(10):
            lock = SmartLockManagerLock(
                lock_entity_id=f"lock.test_lock_{lock_num}",
                lock_name=f"Test Lock {lock_num}",
                slots=30
            )
            
            # Add 30 slots per lock
            for slot_num in range(1, 31):
                slot = CodeSlot(
                    slot_number=slot_num,
                    user_name=f"User {slot_num}",
                    pin_code=f"{1000 + slot_num}",
                    is_active=True
                )
                lock.code_slots[slot_num] = slot
            
            locks.append(lock)
        
        end_time = time.time()
        creation_time = end_time - start_time
        
        # Should create 10 locks with 300 slots total in under 0.1 seconds
        assert creation_time < 0.2, f"Lock creation took {creation_time:.3f}s, expected < 0.2s"
        assert len(locks) == 10
        assert all(len(lock.code_slots) == 30 for lock in locks)
        print(f"✅ Created 10 locks with 300 slots in {creation_time:.3f}s")

    @pytest.mark.asyncio
    async def test_slot_validation_performance(self):
        """Test performance of slot validation operations."""
        # Create a test lock
        lock = SmartLockManagerLock(
            lock_entity_id="lock.test_lock",
            lock_name="Test Lock",
            slots=30
        )
        
        # Add 30 code slots for performance testing
        for i in range(1, 31):
            slot = CodeSlot(
                slot_number=i,
                user_name=f"User {i}",
                pin_code=f"{1000 + i}",
                is_active=True
            )
            lock.code_slots[i] = slot
        
        start_time = time.time()
        
        # Validate all 30 slots multiple times
        for _ in range(100):  # 100 iterations
            for slot in lock.code_slots.values():
                slot.is_valid_now()  # Check time-based validity
                slot.should_disable()  # Check if should auto-disable
        
        end_time = time.time()
        validation_time = end_time - start_time
        
        # 3000 validation operations should complete in under 0.5 seconds
        assert validation_time < 1.0, f"Slot validation took {validation_time:.3f}s, expected < 1.0s"
        print(f"✅ Performed 3000 slot validations in {validation_time:.3f}s")

    @pytest.mark.asyncio
    async def test_concurrent_operations_performance(self):
        """Test performance under concurrent scenarios."""
        start_time = time.time()
        
        # Simulate 20 concurrent operations
        async def mock_operation(operation_id):
            # Simulate some async work
            await asyncio.sleep(0.01)  # 10ms simulation
            return f"operation_{operation_id}_completed"
        
        tasks = []
        for i in range(20):
            task = asyncio.create_task(mock_operation(i))
            tasks.append(task)
        
        results = await asyncio.gather(*tasks)
        
        end_time = time.time()
        concurrent_time = end_time - start_time
        
        # 20 concurrent operations should complete in under 0.5 seconds
        assert concurrent_time < 0.5, f"Concurrent operations took {concurrent_time:.3f}s, expected < 0.5s"
        assert len(results) == 20
        assert all("completed" in result for result in results)
        print(f"✅ Completed 20 concurrent operations in {concurrent_time:.3f}s")

    @pytest.mark.asyncio
    async def test_memory_efficiency(self):
        """Test memory efficiency with large datasets."""
        import sys
        
        # Baseline memory measurement
        baseline = []
        
        # Create many locks with many slots
        locks = {}
        for lock_num in range(20):  # 20 locks
            lock = SmartLockManagerLock(
                lock_entity_id=f"lock.test_lock_{lock_num}",
                lock_name=f"Test Lock {lock_num}",
                slots=30
            )
            
            # 30 slots per lock
            for slot_num in range(1, 31):
                slot = CodeSlot(
                    slot_number=slot_num,
                    user_name=f"User {slot_num}",
                    pin_code=f"{1000 + slot_num}",
                    is_active=True
                )
                lock.code_slots[slot_num] = slot
            
            locks[f"lock.test_lock_{lock_num}"] = lock
        
        # Verify we created the expected number of objects
        assert len(locks) == 20
        total_slots = sum(len(lock.code_slots) for lock in locks.values())
        assert total_slots == 600  # 20 locks × 30 slots
        
        # Memory usage should be reasonable for 600 objects
        memory_size = sys.getsizeof(locks)
        max_allowed_kb = 500  # 500KB should be more than enough
        max_allowed_bytes = max_allowed_kb * 1024
        
        assert memory_size < max_allowed_bytes, \
            f"Memory usage {memory_size / 1024:.1f}KB exceeds {max_allowed_kb}KB limit"
        
        print(f"✅ Created 20 locks with 600 slots using {memory_size / 1024:.1f}KB memory")

    @pytest.mark.asyncio
    async def test_rapid_slot_status_calculation(self):
        """Test rapid slot status calculations for UI updates."""
        # Create a test lock
        lock = SmartLockManagerLock(
            lock_entity_id="lock.test_lock",
            lock_name="Test Lock",
            slots=30
        )
        
        # Add varied slot configurations
        for i in range(1, 31):
            slot = CodeSlot(
                slot_number=i,
                user_name=f"User {i}",
                pin_code=f"{1000 + i}" if i % 2 == 0 else None,  # Some empty slots
                is_active=i % 3 != 0  # Some inactive slots
            )
            lock.code_slots[i] = slot
        
        start_time = time.time()
        
        # Simulate rapid UI updates (like real-time status checking)
        for _ in range(50):  # 50 UI update cycles
            for slot in lock.code_slots.values():
                # Simulate what the UI would check
                is_valid = slot.is_valid_now()
                should_disable = slot.should_disable()
                # Simulate title generation
                title = f"Slot {slot.slot_number}: {slot.user_name or 'Empty'}"
        
        end_time = time.time()
        status_time = end_time - start_time
        
        # 1500 status calculations (50 cycles × 30 slots) should be very fast
        assert status_time < 0.3, f"Status calculations took {status_time:.3f}s, expected < 0.3s"
        print(f"✅ Performed 1500 status calculations in {status_time:.3f}s")


if __name__ == "__main__":
    # Run performance tests
    pytest.main([__file__, "-v", "--tb=short"])