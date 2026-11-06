import type { AxiosRequestConfig } from 'axios'

declare module 'axios' {
  export interface AxiosInstance {
    // 直接调用实例（request({...})）时，返回值就是响应体：
    // src/utils/request.ts 的响应拦截器已经 `return data`（即 res.data）
    <T = any>(config: AxiosRequestConfig): Promise<T>
    <T = any>(url: string, config?: AxiosRequestConfig): Promise<T>

    request<T = any>(config: AxiosRequestConfig): Promise<T>
    get<T = any>(url: string, config?: AxiosRequestConfig): Promise<T>
    delete<T = any>(url: string, config?: AxiosRequestConfig): Promise<T>
    head<T = any>(url: string, config?: AxiosRequestConfig): Promise<T>
    options<T = any>(url: string, config?: AxiosRequestConfig): Promise<T>
    post<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T>
    put<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T>
    patch<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T>
  }
}
