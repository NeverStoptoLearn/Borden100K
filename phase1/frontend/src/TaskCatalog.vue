<script setup>
import {ref,onMounted} from 'vue';
const catalog=ref(null),error=ref('');
onMounted(async()=>{try{const r=await fetch('/api/v1/task-release/tasks');if(!r.ok)throw Error(`HTTP ${r.status}`);catalog.value=await r.json();}catch(e){error.value=String(e);}});
</script>
<template><details class="task-catalog"><summary>Task Release v1.0 RC</summary>
<p>合成、未现场校准。正式大批实验等待容量验收。</p>
<p v-if="error">{{error}}</p><dl v-if="catalog"><template v-for="(task,id) in catalog.tasks" :key="id"><dt>{{id}}</dt><dd>{{task.status}}</dd></template></dl>
</details></template>
<style scoped>.task-catalog{position:fixed;right:12px;bottom:12px;z-index:30;max-width:420px;max-height:65vh;overflow:auto;background:#fff;color:#17253a;border:1px solid #cdd7e1;border-radius:8px;padding:10px;box-shadow:0 4px 20px #0002;font:13px sans-serif}.task-catalog summary{cursor:pointer;font-weight:700}.task-catalog dt{font-weight:600;margin-top:8px}.task-catalog dd{margin-left:12px;color:#45566b}</style>
