module module_pbl_driver
  use module_bl_demo, only: demo_pbl
  implicit none
contains
  subroutine pbl_driver(bl_pbl_physics, qke, tendency)
    integer, intent(in) :: bl_pbl_physics
    real, intent(inout) :: qke
    real, intent(out) :: tendency
    select case (bl_pbl_physics)
    case (5)
      call demo_pbl(qke, tendency)
    end select
  end subroutine pbl_driver
end module module_pbl_driver
