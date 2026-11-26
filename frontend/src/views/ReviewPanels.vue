<template>
  <!-- 样本量核对详情：选择一轮下载、提交更正说明、辨认哪一版可用于后续处置 -->
  <section v-if="mode === 'sample_ratio'" class="page review-panel" data-testid="sample-review-panel">
    <h3>外部复核下载与更正</h3>
    <div v-if="rounds.length" class="round-list">
      <article
        v-for="roundItem in rounds"
        :key="roundItem.核对版次"
        class="round-card"
        :data-testid="`round-${roundItem.核对版次}`"
        :class="{ usable: roundItem.可用于后续处置 }"
      >
        <header>
          <strong>{{ roundItem.核对版次 }}</strong>
          <span class="badge">{{ roundItem.已下载 ? '已下载' : '未下载' }}</span>
          <span v-if="roundItem.可用于后续处置" class="badge ok">可用于后续处置</span>
        </header>
        <p>
          计划比例 {{ roundItem.计划比例 }}，实验组 {{ roundItem.实验组计数 }} / 对照组
          {{ roundItem.对照组计数 }}，阈值 {{ roundItem.阈值 }}
        </p>
        <p class="muted">{{ roundItem.说明 }}</p>
        <div class="page-actions">
          <button
            class="btn"
            type="button"
            :data-testid="`download-${roundItem.核对版次}`"
            @click="downloadRound(roundItem)"
          >
            下载 {{ roundItem.核对版次 }}
          </button>
          <button
            v-if="roundItem.文件编号"
            class="btn ghost"
            type="button"
            :data-testid="`fetch-${roundItem.核对版次}`"
            @click="fetchFile(roundItem.文件编号)"
          >
            取文件内容核验
          </button>
        </div>
      </article>
    </div>
    <p v-else class="muted">暂无可下载轮次</p>

    <div v-if="file" class="file-box" data-testid="review-file">
      <h4>复核文件 {{ file.文件编号 }}</h4>
      <dl class="detail-grid">
        <dt>实验编码</dt><dd>{{ file.实验编码 }}</dd>
        <dt>核对版次</dt><dd>{{ file.核对版次 }}</dd>
        <dt>计划比例</dt><dd>{{ file.计划比例 }}（计划实验组 {{ file.计划实验组人数 }} 人）</dd>
        <dt>两组计数</dt><dd>实验组 {{ file.实验组计数 }} / 对照组 {{ file.对照组计数 }}</dd>
        <dt>样本总量</dt><dd>{{ file.样本总量 }}</dd>
        <dt>实际比例</dt><dd>{{ file.实际比例 }}</dd>
        <dt>偏移量 / 阈值</dt><dd>{{ file.偏移量 }} / {{ file.阈值 }}</dd>
      </dl>
      <pre v-if="file.文件内容" class="file-content" data-testid="file-content">{{ file.文件内容 }}</pre>
    </div>

    <form v-if="file" class="inline-form" @submit.prevent="submitCorrection">
      <h4>提交更正说明</h4>
      <label class="create-field">
        <span>核对版次</span>
        <input v-model="correction.revision" :data-testid="'evidence-revision'" required />
      </label>
      <label class="create-field">
        <span>实验编码</span>
        <input v-model="correction.experimentCode" :data-testid="'evidence-experiment'" required />
      </label>
      <label class="create-field">
        <span>复核人</span>
        <input v-model="correction.reviewer" :data-testid="'evidence-reviewer'" />
      </label>
      <label class="create-field">
        <span>更正后实验组计数</span>
        <input v-model="correction.arm" :data-testid="'evidence-arm'" inputmode="numeric" />
      </label>
      <label class="create-field">
        <span>更正后对照组计数</span>
        <input v-model="correction.ctrl" :data-testid="'evidence-ctrl'" inputmode="numeric" />
      </label>
      <label class="create-field">
        <span>更正调整人数</span>
        <input v-model="correction.moved" :data-testid="'evidence-moved'" inputmode="numeric" />
      </label>
      <label class="create-field">
        <span>转入组别</span>
        <input v-model="correction.target" :data-testid="'evidence-target'" placeholder="对照组" />
      </label>
      <label class="create-field">
        <span>更正分子 / 分母</span>
        <input v-model="correction.numerator" :data-testid="'evidence-numerator'" inputmode="numeric" />
        <input v-model="correction.denominator" :data-testid="'evidence-denominator'" inputmode="numeric" />
      </label>
      <label class="create-field">
        <span>说明</span>
        <input v-model="correction.note" :data-testid="'evidence-note'" />
      </label>
      <div class="page-actions">
        <button class="btn primary" data-testid="submit-evidence" type="submit">登记更正证据</button>
      </div>
    </form>

    <div v-if="evidenceResult" class="result-box" data-testid="evidence-result">
      <p :class="evidenceResult.ok ? '' : 'error-text'">{{ evidenceResult.message }}</p>
      <ul v-if="evidenceResult.errors?.length" class="error-list">
        <li v-for="item in evidenceResult.errors" :key="item">{{ item }}</li>
      </ul>
      <pre v-if="evidenceResult.raw_row" data-testid="raw-row">{{ JSON.stringify(evidenceResult.raw_row, null, 2) }}</pre>
      <p v-if="evidenceResult.evidence" data-testid="evidence-summary">
        证据 {{ evidenceResult.evidence.证据编号 }}：实验组 {{ evidenceResult.evidence.实验组计数 }} /
        对照组 {{ evidenceResult.evidence.对照组计数 }}，偏移
        {{ (evidenceResult.evidence.复算 as Record<string, unknown> | undefined)?.['偏移量'] }}
      </p>
      <p v-if="evidenceResult.error_record" class="muted" data-testid="error-record">
        错误已记录：{{ evidenceResult.error_record['错误编号'] }}
      </p>
      <button
        v-if="evidenceResult.ok && evidenceResult.evidence"
        class="btn primary"
        type="button"
        :data-testid="'claim-evidence'"
        @click="claim(evidenceResult.evidence.证据编号)"
      >
        登记签收并领取凭证
      </button>
      <p v-if="claimMessage" data-testid="claim-message">{{ claimMessage }}</p>
    </div>
    <p v-if="errorMessage" class="error-text" data-testid="review-error">{{ errorMessage }}</p>
  </section>

  <!-- 异常检测页：打开已登记凭证办理，并展示采用哪套计数口径 -->
  <section v-else class="page review-panel" data-testid="anomaly-review-panel">
    <h3>异常处置凭证</h3>
    <p v-if="anomalyNotice" class="muted" data-testid="anomaly-notice">{{ anomalyNotice }}</p>
    <div v-if="anomalyFile" class="file-box" data-testid="anomaly-file">
      <dl class="detail-grid">
        <dt>关联实验</dt><dd>{{ anomalyFile.实验编码 }}</dd>
        <dt>核对版次</dt><dd>{{ anomalyFile.核对版次 }}</dd>
        <dt>文件编号</dt><dd>{{ anomalyFile.文件编号 }}</dd>
        <dt>下载时计数</dt>
        <dd>实验组 {{ anomalyFile.实验组计数 }} / 对照组 {{ anomalyFile.对照组计数 }}</dd>
        <dt>计划比例 / 阈值</dt><dd>{{ anomalyFile.计划比例 }} / {{ anomalyFile.阈值 }}</dd>
      </dl>
    </div>
    <div v-if="ticket" class="file-box" data-testid="ticket-box">
      <dl class="detail-grid">
        <dt>凭证编号</dt><dd>{{ ticket.凭证编号 }}</dd>
        <dt>锁定证据</dt><dd>{{ ticket.证据编号 }}（{{ ticket.采用计数口径 }}）</dd>
        <dt>下载时计数</dt><dd>{{ ticket.下载实验组计数 }} / {{ ticket.下载对照组计数 }}</dd>
        <dt>修订计数</dt><dd>{{ ticket.修订实验组计数 }} / {{ ticket.修订对照组计数 }}</dd>
        <dt>计划比例 / 阈值</dt><dd>{{ ticket.计划比例 }} / {{ ticket.阈值 }}</dd>
        <dt>状态</dt><dd>{{ ticket.status }}</dd>
      </dl>
      <p v-if="ticket.计数口径冲突" class="muted" data-testid="ticket-conflict">{{ ticket.计数口径冲突 }}</p>
      <p v-if="recompute" class="recompute" data-testid="ticket-recompute">
        重算：下载时偏移 {{ recompute.下载偏移量 }}（{{ recompute.下载超阈值 ? '超' : '未超' }}阈值），
        修订后偏移 {{ recompute.修订偏移量 }}（{{ recompute.修订超阈值 ? '超' : '未超' }}阈值），阈值 {{ recompute.阈值 }}
      </p>
      <div v-if="ticket.status === '待处置'" class="page-actions">
        <button class="btn primary" type="button" data-testid="settle-confirm" @click="settle(false)">确认异常</button>
        <button class="btn" type="button" data-testid="settle-close" @click="settle(true)">按修订计数关闭告警</button>
        <label class="inline-check">
          <input v-model="governor" type="checkbox" data-testid="settle-governor" /> 同时启动偏移治理
        </label>
      </div>
      <div v-else class="result-box" data-testid="settle-result">
        <p>{{ ticket.办结结论 }}</p>
        <dl v-if="ticket.办结依据" class="detail-grid">
          <dt>实际采用计数口径</dt><dd>{{ ticket.办结依据['采用计数口径'] }}</dd>
          <dt>采用证据</dt><dd>{{ ticket.办结依据['采用证据'] }}</dd>
          <dt>下载时偏移</dt><dd>{{ ticket.办结依据['下载时偏移'] }}</dd>
          <dt>修订后偏移</dt><dd>{{ ticket.办结依据['修订后偏移'] }}</dd>
          <dt>阈值</dt><dd>{{ ticket.办结依据['阈值'] }}</dd>
          <dt>是否启动偏移治理</dt><dd>{{ ticket.办结依据['是否启动偏移治理'] ? '是' : '否' }}</dd>
        </dl>
      </div>
    </div>
    <p v-else-if="!anomalyNotice" class="muted">该告警尚无已登记的处置凭证</p>
    <p v-if="errorMessage" class="error-text" data-testid="review-error">{{ errorMessage }}</p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import {
  claimTicket,
  createDownload,
  downloadReviewFile,
  fetchFileAudit,
  loadAnomalyMaterial,
  loadTaskDetail,
  loadTicketDetail,
  settleTicket,
  submitEvidence,
} from '@/api/review-service'
import type { ReviewFile, ReviewRound, ReviewEvidence, ReviewTicket } from '@/api/review-service'
import type { EntryRow } from '@/data/types'

const props = defineProps<{
  mode: 'sample_ratio' | 'anomaly_metric'
  row: EntryRow | null
}>()

const errorMessage = ref('')
const rounds = ref<ReviewRound[]>([])
const file = ref<ReviewFile | null>(null)
const files = ref<ReviewFile[]>([])
const claimMessage = ref('')
const ticket = ref<ReviewTicket | null>(null)
const recompute = ref<Record<string, unknown> | null>(null)
const anomalyFile = ref<Record<string, unknown> | null>(null)
const anomalyNotice = ref('')
const governor = ref(false)
const evidenceResult = ref<{
  ok: boolean
  message: string
  errors?: string[]
  raw_row?: Record<string, unknown>
  error_record?: Record<string, unknown>
  evidence?: ReviewEvidence
} | null>(null)

const correction = ref({
  revision: '',
  experimentCode: '',
  reviewer: '外部复核组',
  arm: '',
  ctrl: '',
  moved: '',
  target: '对照组',
  numerator: '',
  denominator: '',
  note: '',
})

const rowId = computed(() => (props.row ? Number(props.row.id) : null))

function reset() {
  errorMessage.value = ''
  rounds.value = []
  file.value = null
  files.value = []
  claimMessage.value = ''
  ticket.value = null
  recompute.value = null
  anomalyFile.value = null
  anomalyNotice.value = ''
  evidenceResult.value = null
  governor.value = false
}

async function loadSample() {
  if (!rowId.value) return
  try {
    const detail = await loadTaskDetail(rowId.value)
    rounds.value = detail.rounds
    files.value = detail.files
    const rowRevision = String(props.row?.['核对版次'] ?? '')
    const target = rounds.value.find((item) => item.核对版次 === rowRevision) ?? rounds.value[0]
    file.value =
      detail.files.find((item) => item.核对版次 === (target?.核对版次 ?? rowRevision)) ??
      detail.files[0] ??
      null
    if (target) {
      correction.value.revision = target.核对版次
      correction.value.experimentCode = target.实验编码
      correction.value.arm = target.实验组计数
      correction.value.ctrl = target.对照组计数
      correction.value.numerator = target.实验组计数
      correction.value.denominator = String(Number(target.实验组计数) + Number(target.对照组计数))
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '核对详情读取失败'
  }
}

async function loadAnomaly() {
  if (!rowId.value) return
  try {
    const material = await loadAnomalyMaterial(rowId.value)
    anomalyNotice.value = String(material.message ?? '')
    anomalyFile.value = (material.file as Record<string, unknown> | null) ?? null
    const tickets = (material.tickets as ReviewTicket[] | undefined) ?? []
    ticket.value = (material.active_ticket as ReviewTicket | null) ?? tickets[tickets.length - 1] ?? null
    if (ticket.value) {
      const detail = await loadTicketRecompute(ticket.value.凭证编号)
      recompute.value = detail
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '异常复核材料读取失败'
  }
}

async function loadTicketRecompute(ticketId: string) {
  // 凭证详情由后端复算，避免前端重复实现阈值逻辑。
  try {
    const payload = await loadTicketDetail(ticketId)
    ticket.value = payload.ticket
    return payload.recompute as unknown as Record<string, unknown>
  } catch {
    return null
  }
}

async function downloadRound(target: ReviewRound) {
  if (!rowId.value) return
  errorMessage.value = ''
  try {
    const result = await createDownload(rowId.value, {
      revision: target.核对版次,
      planned_ratio: Number(target.计划比例),
      experiment_count: Number(target.实验组计数),
      control_count: Number(target.对照组计数),
      threshold: Number(target.阈值),
    })
    if (!result.ok) throw new Error(result.message)
    if (result.file) {
      file.value = result.file
      await downloadReviewFile(result.file.文件编号, result.file.文件编号)
    }
    await loadSample()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '下载失败'
  }
}

async function fetchFile(fileId: string) {
  errorMessage.value = ''
  try {
    const audit = await fetchFileAudit(fileId)
    file.value = audit.entry as ReviewFile
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '文件读取失败'
  }
}

async function submitCorrection() {
  if (!file.value) return
  errorMessage.value = ''
  evidenceResult.value = null
  claimMessage.value = ''
  try {
    const result = await submitEvidence(file.value.文件编号, {
      revision: correction.value.revision,
      experiment_code: correction.value.experimentCode,
      reviewer: correction.value.reviewer,
      group_counts: {
        实验组: correction.value.arm,
        对照组: correction.value.ctrl,
      },
      submitted_numerator: correction.value.numerator ? Number(correction.value.numerator) : null,
      submitted_denominator: correction.value.denominator ? Number(correction.value.denominator) : null,
      adjustment: correction.value.moved
        ? { 人数: Number(correction.value.moved), 转入组别: correction.value.target }
        : null,
      note: correction.value.note,
    })
    evidenceResult.value = {
      ok: result.ok,
      message: result.message,
      errors: result.errors,
      raw_row: result.raw_row,
      error_record: result.error_record,
      evidence: result.evidence,
    }
    await loadSample()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '更正登记失败'
  }
}

async function claim(evidenceId: string) {
  errorMessage.value = ''
  try {
    const result = await claimTicket(evidenceId, undefined)
    if (!result.ok) throw new Error(result.message)
    claimMessage.value = `${result.message}；可在异常检测页办理`
    await loadSample()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '领取凭证失败'
  }
}

async function settle(close: boolean) {
  if (!ticket.value) return
  errorMessage.value = ''
  try {
    const result = await settleTicket(ticket.value.凭证编号, {
      decision: close ? 'close' : 'confirm',
      governor: governor.value,
      operator: '数据质量组',
    })
    if (!result.ok) throw new Error(result.message)
    ticket.value = result.ticket ?? ticket.value
    recompute.value = (result.recompute as Record<string, unknown>) ?? recompute.value
    await loadAnomaly()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '办结失败'
  }
}

watch(
  () => [props.mode, rowId.value] as const,
  () => {
    reset()
    if (props.mode === 'sample_ratio') void loadSample()
    else void loadAnomaly()
  },
  { immediate: true },
)
</script>
