/** 外部复核接口封装：核对轮次、下载、更正登记、凭证领取与办结。 */
import { request } from './client'

export type ReviewRound = {
  核对记录ID: number
  核对批次: string
  实验编码: string
  核对版次: string
  计划比例: string
  实验组计数: string
  对照组计数: string
  阈值: string
  偏移量: string
  状态: string
  已下载: boolean
  文件编号: string | null
  可用于后续处置: boolean
  说明: string
}

export type ReviewFile = Record<string, unknown> & {
  文件编号: string
  实验编码: string
  核对版次: string
  计划比例: string
  计划实验组人数: string
  实验组计数: string
  对照组计数: string
  样本总量: string
  实际比例: string
  偏移量: string
  阈值: string
  文件内容?: string
}

export type ReviewEvidence = Record<string, unknown> & {
  证据编号: string
  文件编号: string
  实验编码: string
  核对版次: string
  实验组计数: string
  对照组计数: string
  有效: boolean
  采用计数口径: string
  复算?: Record<string, unknown>
}

export type ReviewTicket = Record<string, unknown> & {
  凭证编号: string
  证据编号: string
  文件编号: string
  实验编码: string
  核对版次: string
  异常编号: string
  计划比例: string
  下载实验组计数: string
  下载对照组计数: string
  修订实验组计数: string
  修订对照组计数: string
  阈值: string
  status: string
  采用计数口径?: string
  办结依据?: Record<string, unknown>
}

export type TaskDetail = {
  ok: boolean
  message: string
  entry: Record<string, unknown>
  rounds: ReviewRound[]
  files: ReviewFile[]
}

export type Recompute = {
  计划比例: string
  下载实验组计数?: number
  下载对照组计数?: number
  下载实际比例?: string
  下载偏移量?: string
  下载超阈值?: boolean
  修订实验组计数?: number
  修订对照组计数?: number
  修订实际比例?: string
  修订偏移量?: string
  修订超阈值?: boolean
  阈值: string
}

export type ReviewResult<T> = {
  ok: boolean
  message: string
  errors?: string[]
  raw_row?: Record<string, unknown>
  error_record?: Record<string, unknown>
  file?: ReviewFile
  evidence?: ReviewEvidence
  ticket?: ReviewTicket
  recompute?: Recompute
  basis?: Record<string, unknown>
  anomaly?: Record<string, unknown>
  immutable?: boolean
} & T

async function postJson<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  if (!response.ok) throw new Error(`接口返回 ${response.status}，数据未更新`)
  return (await response.json()) as T
}

export async function loadTaskDetail(entryId: number): Promise<TaskDetail> {
  const response = await request(`/api/review/tasks/${entryId}`)
  if (!response.ok) throw new Error('核对详情读取失败')
  return (await response.json()) as TaskDetail
}

export async function createDownload(
  entryId: number,
  body: Record<string, unknown>,
): Promise<ReviewResult<{ file?: ReviewFile }>> {
  return await postJson(`/api/review/tasks/${entryId}/downloads`, body)
}

export async function submitEvidence(
  fileId: string,
  body: Record<string, unknown>,
): Promise<ReviewResult<{ evidence?: ReviewEvidence }>> {
  return await postJson(`/api/review/files/${encodeURIComponent(fileId)}/evidence`, body)
}

export async function fetchFileAudit(fileId: string): Promise<Record<string, unknown>> {
  const response = await request(`/api/review/files/${encodeURIComponent(fileId)}/audit`)
  if (!response.ok) throw new Error('复核留档读取失败')
  return await response.json()
}

export async function downloadReviewFile(fileId: string, filename: string): Promise<void> {
  const response = await request(`/api/review/files/${encodeURIComponent(fileId)}/downloads`)
  if (!response.ok) throw new Error('复核文件下载失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${filename}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function claimTicket(
  evidenceId: string,
  anomalyEntryId?: number,
): Promise<ReviewResult<{ ticket?: ReviewTicket }>> {
  return await postJson(`/api/review/evidence/${encodeURIComponent(evidenceId)}/claim`, {
    anomaly_entry_id: anomalyEntryId ?? null,
    operator: '数据质量组',
  })
}

export async function settleTicket(
  ticketId: string,
  body: Record<string, unknown>,
): Promise<ReviewResult<{ ticket?: ReviewTicket }>> {
  return await postJson(`/api/review/tickets/${encodeURIComponent(ticketId)}/settle`, body)
}

export async function loadAnomalyMaterial(entryId: number): Promise<Record<string, unknown>> {
  const response = await request(`/api/review/anomalies/${entryId}`)
  if (!response.ok) throw new Error('异常复核材料读取失败')
  return await response.json()
}

export async function loadTicketDetail(ticketId: string): Promise<{
  ok: boolean
  ticket: ReviewTicket
  recompute: Recompute
  basis: Record<string, unknown> | null
}> {
  const response = await request(`/api/review/tickets/${encodeURIComponent(ticketId)}`)
  if (!response.ok) throw new Error('处置凭证读取失败')
  return await response.json()
}

export async function loadDisposition(identifier: string): Promise<Record<string, unknown>> {
  const response = await request(`/api/review/records/${encodeURIComponent(identifier)}`)
  if (!response.ok) throw new Error('处置记录读取失败')
  return await response.json()
}
