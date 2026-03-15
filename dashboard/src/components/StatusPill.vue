<script setup>
defineProps(['status'])
</script>

<template>
  <span :class="`badge badge-${status}`">
    <span :class="`dot dot-${status}`" />
    {{ status }}
  </span>
</template>
