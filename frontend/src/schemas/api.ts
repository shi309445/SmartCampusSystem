/** 后端统一响应体 app/common/response.py: Response[T] */
export interface ApiResponse<T = unknown> {
  code: number
  message: string
  data: T
}

/** 后端统一分页结构 app/schemas/models.py: PageResponse[T] */
export interface PageData<T> {
  total: number
  page: number
  page_size: number
  data: T[]
}
