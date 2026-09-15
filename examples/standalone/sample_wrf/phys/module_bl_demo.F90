module module_bl_demo
  implicit none
contains
  subroutine demo_pbl(qke, tendency)
    real, intent(inout) :: qke
    real, intent(out) :: tendency
    call compute_tendency(qke, tendency)
    qke = qke + tendency
  end subroutine demo_pbl

  subroutine compute_tendency(qke, tendency)
    real, intent(in) :: qke
    real, intent(out) :: tendency
    tendency = -0.1 * qke
  end subroutine compute_tendency
end module module_bl_demo
