function mark() {
    document.getElementById("result").innerHTML = "⏳ Scanning...";

    fetch('/mark')
    .then(res => res.json())
    .then(data => {
        document.getElementById("result").innerHTML =
            `✅ <b>${data.name}</b><br><small>${data.time}</small>`;
    });
}
// SEARCH
document.addEventListener("DOMContentLoaded", ()=>{
let input = document.getElementById("search");
if(input){
input.addEventListener("keyup", ()=>{
let val = input.value.toLowerCase();
document.querySelectorAll("table tr").forEach(row=>{
row.style.display = row.innerText.toLowerCase().includes(val) ? "" : "none";
});
});
}

// CHART
fetch('/stats')
.then(res=>res.json())
.then(data=>{
let names = data.map(d=>d[0]);
let count = data.map(d=>d[1]);

new Chart(document.getElementById("chart"),{
type:'bar',
data:{labels:names,datasets:[{data:count}]}
});
});
});